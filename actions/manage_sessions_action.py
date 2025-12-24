from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from base_classes import InteractionAction
from core.session_persistence import (
    apply_session_data,
    list_sessions,
    load_session_data,
    resolve_session_path,
    save_session,
)


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
                model = it.get('model') or ''
                snippet = it.get('first_user') or ''
                ts = it.get('updated') or it.get('created') or it.get('mtime') or 0.0
                when = ''
                if ts:
                    try:
                        when = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(ts)))
                    except Exception:
                        when = ''
                msg = f"- {name} [{kind}]"
                if when:
                    msg += f" {when}"
                if model:
                    msg += f" model={model}"
                if title:
                    msg += f" — {title}"
                if snippet:
                    msg += f" — \"{snippet}\""
                self.session.ui.emit('status', {'message': msg})
        except Exception:
            pass
        return {'ok': True, 'sessions': items}

    def _resume_session(self, target: str) -> Dict[str, Any]:
        if not target:
            return {'ok': False, 'error': 'missing_target'}
        path = resolve_session_path(self.session, target)
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
