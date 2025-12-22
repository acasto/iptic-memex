from __future__ import annotations

import os
from typing import Any, Dict, List

from base_classes import InteractionAction
from core.session_persistence import apply_session_data, list_sessions, load_session_data, save_session


class ManageSessionsAction(InteractionAction):
    """List, resume, and checkpoint persistent sessions."""

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        args = args or []
        if isinstance(args, str):
            args = [args]
        if not args:
            return {'ok': False, 'error': 'missing_mode'}
        mode = str(args[0]).strip().lower()
        if mode == 'list':
            return self._list_sessions()
        if mode == 'resume':
            target = args[1] if len(args) > 1 else ''
            return self._resume_session(target)
        if mode == 'checkpoint':
            title = args[1] if len(args) > 1 else ''
            return self._checkpoint_session(title)
        return {'ok': False, 'error': 'invalid_mode'}

    def _list_sessions(self) -> Dict[str, Any]:
        items = list_sessions(self.session)
        try:
            self.session.ui.emit('status', {'message': 'Sessions:'})
            if not items:
                self.session.ui.emit('status', {'message': '(none)'})
            for it in items:
                name = it.get('id') or it.get('path') or ''
                kind = it.get('kind') or 'session'
                title = it.get('title') or ''
                suffix = f" — {title}" if title else ""
                self.session.ui.emit('status', {'message': f"- {name} [{kind}]{suffix}"})
        except Exception:
            pass
        return {'ok': True, 'sessions': items}

    def _resume_session(self, target: str) -> Dict[str, Any]:
        if not target:
            return {'ok': False, 'error': 'missing_target'}
        path = self._resolve_session_path(target)
        if not path or not os.path.isfile(path):
            return {'ok': False, 'error': 'not_found'}
        data = load_session_data(path)
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

    def _resolve_session_path(self, target: str) -> str:
        # Direct path
        if os.path.isfile(target):
            return target
        # Try id-based lookup in session directory
        items = list_sessions(self.session)
        for it in items:
            if it.get('id') == target:
                return it.get('path') or ''
        # Fallback: match filename prefix
        for it in items:
            path = it.get('path') or ''
            if os.path.basename(path).startswith(target):
                return path
        return ''
