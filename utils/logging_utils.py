from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _safe_str(x: Any) -> str:
    try:
        return str(x)
    except Exception:
        return '<unprintable>'


class LoggingHandler:
    """
    Centralized, configurable logging sink with per-aspect gating.

    - Format: JSONL or plain text
    - File policy: per-run timestamped file in [LOG].dir or explicit [LOG].file
    - Console mirror: optional via OutputHandler (when provided)
    - Redaction & truncation: applied to data payloads
    """

    # Aspect level mapping
    _LEVELS = {  # numeric for comparisons
        'off': 0,
        'minimal': 1,
        'basic': 1,
        'detail': 2,
        'trace': 3,
    }

    _DEFAULTS = {  # default levels when aspect unset and no global verbosity
        'settings': 'basic',
        'messages': 'off',
        'tool_use': 'basic',
        'cmd': 'minimal',
        'provider': 'basic',
        'mcp': 'off',
        'rag': 'off',
        'actions': 'off',
        'tui': 'off',
        'web': 'off',
        'errors': 'basic',
        'usage': 'basic',
    }

    def __init__(self, config, output_handler=None) -> None:
        self._config = config
        self._output = output_handler
        self._active: bool = bool(self._get('active', False))
        self._format: str = (self._get('format', 'json') or 'json').strip().lower()
        if self._format not in ('json', 'text'):
            self._format = 'json'
        self._mirror: bool = bool(self._get('mirror_to_console', False))
        self._redact: bool = bool(self._get('redact', True))
        self._truncate: int = int(self._get('truncate_chars', 2000) or 2000)
        self._verbosity_base: Optional[str] = (self._get('verbosity', None) or None)
        if isinstance(self._verbosity_base, str):
            self._verbosity_base = self._verbosity_base.strip().lower()
        # Pre-parse per-aspect levels
        self._aspects: Dict[str, int] = {}
        for asp, default in self._DEFAULTS.items():
            raw = self._get(f'log_{asp}', None)
            level_name = None
            if isinstance(raw, str) and raw.strip():
                level_name = raw.strip().lower()
            elif isinstance(self._verbosity_base, str):
                level_name = self._verbosity_base
            else:
                level_name = default
            self._aspects[asp] = self._LEVELS.get(level_name, self._LEVELS['off'])

        # File handling
        self._run_id = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        self._log_path = None
        if self._active:
            self._log_path = self._open_logfile()
        self._write = self._writer_json if self._format == 'json' else self._writer_text

    # --- Public helpers -------------------------------------------------
    def active(self) -> bool:
        return bool(self._active and self._log_path)

    # Generic entry point
    def log(self, event: str, *, component: str, aspect: str, severity: str = 'info', data: Optional[dict] = None) -> None:
        if not self._should_log(aspect, 'basic'):
            return
        payload = self._prepare_payload(event, component, aspect, severity, data or {})
        self._write(payload)

    def is_enabled(self, aspect: str, min_level: str = 'basic') -> bool:
        """Return True if logging is active and the given aspect meets the min level."""
        return self._should_log(aspect, min_level)

    # Settings/events sugar
    def settings(self, effective: dict) -> None:
        if not self._should_log('settings', 'basic'): return
        self._write(self._prepare_payload('settings', 'core.session', 'settings', 'info', effective))

    def provider_start(self, meta: dict, component: str = 'core.turns') -> None:
        if not self._should_log('provider', 'basic'): return
        self._write(self._prepare_payload('provider_start', component, 'provider', 'info', meta))

    def provider_done(self, meta: dict, component: str = 'core.turns') -> None:
        if not self._should_log('provider', 'basic'): return
        self._write(self._prepare_payload('provider_done', component, 'provider', 'info', meta))

    def tool_begin(self, name: str, call_id: Optional[str] = None, args_summary: Optional[dict] = None, source: str = 'official') -> None:
        if not self._should_log('tool_use', 'basic'): return
        data = {'name': name, 'call_id': call_id, 'source': source}
        if args_summary is not None:
            data['args'] = args_summary
        self._write(self._prepare_payload('tool_begin', 'core.turns', 'tool_use', 'info', data))

    def tool_end(self, name: str, call_id: Optional[str] = None, status: str = 'success', result_meta: Optional[dict] = None) -> None:
        if not self._should_log('tool_use', 'basic'): return
        data = {'name': name, 'call_id': call_id, 'status': status}
        if result_meta is not None:
            data['result'] = result_meta
        self._write(self._prepare_payload('tool_end', 'core.turns', 'tool_use', 'info', data))

    def cmd_exec(self, cmd: str, args: str, cwd: str, pipeline: list[str]):
        if not self._should_log('cmd', 'minimal'): return
        data = {'cmd': cmd, 'args': args, 'cwd': cwd, 'pipeline': pipeline}
        self._write(self._prepare_payload('cmd_exec', 'actions.cmd', 'cmd', 'info', data))

    def cmd_result(self, exit_code: int, stdout: Optional[str], stderr: Optional[str], duration_ms: int):
        if not self._should_log('cmd', 'minimal'): return
        # Respect detail/trace levels for previews
        lvl = self._level_for('cmd')
        previews = {}
        if lvl >= self._LEVELS['detail']:
            previews['stdout_preview'] = (stdout or '')
            previews['stderr_preview'] = (stderr or '')
        data = {
            'exit_code': exit_code,
            'stdout_len': len(stdout or ''),
            'stderr_len': len(stderr or ''),
            'duration_ms': duration_ms,
            **({'previews': previews} if previews else {})
        }
        self._write(self._prepare_payload('cmd_result', 'actions.cmd', 'cmd', 'info', data))

    def messages_summary(self, meta: dict):
        if not self._should_log('messages', 'basic'): return
        self._write(self._prepare_payload('messages', 'core.turns', 'messages', 'info', meta))

    def messages_event(self, kind: str, details: dict, component: str = 'core.messages'):
        if not self._should_log('messages', 'basic'): return
        self._write(self._prepare_payload(kind, component, 'messages', 'info', details))

    def messages_detail(self, kind: str, details: dict, component: str = 'core.messages'):
        if not self._should_log('messages', 'detail'): return
        self._write(self._prepare_payload(kind, component, 'messages', 'info', details))

    def mcp_event(self, kind: str, details: dict, component: str = 'provider'):
        if not self._should_log('mcp', 'basic'): return
        self._write(self._prepare_payload(kind, f'{component}.mcp', 'mcp', 'info', details))

    def mcp_detail(self, kind: str, details: dict, component: str = 'provider'):
        if not self._should_log('mcp', 'detail'): return
        self._write(self._prepare_payload(kind, f'{component}.mcp', 'mcp', 'info', details))

    def rag_event(self, kind: str, details: dict, component: str = 'rag'):
        if not self._should_log('rag', 'basic'): return
        self._write(self._prepare_payload(kind, f'{component}', 'rag', 'info', details))

    def tui_event(self, kind: str, details: dict, component: str = 'tui'):
        if not self._should_log('tui', 'basic'): return
        self._write(self._prepare_payload(kind, f'{component}', 'tui', 'info', details))

    def tui_detail(self, kind: str, details: dict, component: str = 'tui'):
        """TUI detail-level helper. Emits only when [LOG].log_tui >= detail."""
        if not self._should_log('tui', 'detail'): return
        self._write(self._prepare_payload(kind, f'{component}', 'tui', 'info', details))

    # Actions (mode-agnostic) -------------------------------------------------
    def action_event(self, kind: str, details: dict, component: str = 'action'):
        if not self._should_log('actions', 'basic'): return
        self._write(self._prepare_payload(kind, f'{component}', 'actions', 'info', details))

    def action_detail(self, kind: str, details: dict, component: str = 'action'):
        if not self._should_log('actions', 'detail'): return
        self._write(self._prepare_payload(kind, f'{component}', 'actions', 'info', details))

    def web_event(self, kind: str, details: dict, component: str = 'web'):
        if not self._should_log('web', 'basic'): return
        self._write(self._prepare_payload(kind, f'{component}', 'web', 'info', details))

    def error(self, where: str, exc: BaseException, *, stack: Optional[str] = None):
        if not self._should_log('errors', 'basic'): return
        s = stack or ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._write(self._prepare_payload('error', where, 'errors', 'error', {'message': _safe_str(exc), 'stack': s}))

    def usage(self, aggregate: dict):
        if not self._should_log('usage', 'basic'): return
        self._write(self._prepare_payload('usage', 'core.usage', 'usage', 'info', aggregate))

    # --- Internals ------------------------------------------------------
    def _get(self, key: str, fallback: Any = None) -> Any:
        try:
            return self._config.get_option('LOG', key, fallback)
        except Exception:
            return fallback

    def _open_logfile(self) -> Optional[str]:
        try:
            # Determine application root (directory containing main.py; one level above utils/)
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            explicit = (self._get('file', '') or '').strip()
            per_run = bool(self._get('per_run', True))
            raw_dir = self._get('dir', 'logs') or 'logs'

            # Resolve directory: absolute stays; relative -> app_root/<dir>
            raw_dir = os.path.expanduser(raw_dir)
            log_dir = raw_dir if os.path.isabs(raw_dir) else os.path.join(app_root, raw_dir)
            os.makedirs(log_dir, exist_ok=True)

            # Resolve explicit file path when provided
            if explicit:
                explicit = os.path.expanduser(explicit)
                path = explicit if os.path.isabs(explicit) else os.path.join(log_dir, explicit)
            else:
                filename = f'memex-{self._run_id}.log' if per_run else 'memex.log'
                path = os.path.join(log_dir, filename)

            # Ensure parent dir exists for the file (in case explicit includes subfolders)
            os.makedirs(os.path.dirname(path) or log_dir, exist_ok=True)

            # Touch file
            with open(path, 'a', encoding='utf-8'):
                pass

            # Manage latest symlink optionally (keep inside resolved log_dir)
            if bool(self._get('symlink_latest', True)):
                try:
                    latest = os.path.join(log_dir, 'latest.log')
                    if os.path.islink(latest) or os.path.exists(latest):
                        try:
                            os.remove(latest)
                        except Exception:
                            pass
                    os.symlink(os.path.abspath(path), latest)
                except Exception:
                    pass
            return path
        except Exception:
            return None

    def _level_for(self, aspect: str) -> int:
        return self._aspects.get(aspect, 0)

    def _should_log(self, aspect: str, min_level_name: str) -> bool:
        if not self._active or not self._log_path:
            return False
        lvl = self._level_for(aspect)
        required = self._LEVELS.get(min_level_name, 1)
        return lvl >= required

    def _redact_and_truncate(self, data: Any) -> Any:
        keys = []
        try:
            raw = self._get('redact_keys', None)
            if isinstance(raw, str) and raw.strip():
                keys = [k.strip().lower() for k in raw.split(',') if k.strip()]
            else:
                keys = ['api_key', 'authorization', 'token', 'password', 'secret', 'key']
        except Exception:
            keys = ['api_key', 'authorization', 'token', 'password', 'secret', 'key']

        def _walk(obj: Any) -> Any:
            # Truncate long strings
            if isinstance(obj, str):
                if self._truncate and len(obj) > self._truncate:
                    return obj[: self._truncate] + '…'
                return obj
            if isinstance(obj, dict):
                out = {}
                for k, v in obj.items():
                    kk = _safe_str(k)
                    if self._redact and kk.lower() in keys:
                        out[kk] = '***redacted***'
                    else:
                        out[kk] = _walk(v)
                return out
            if isinstance(obj, list):
                return [_walk(x) for x in obj]
            return obj

        try:
            return _walk(data)
        except Exception:
            return data

    def _prepare_payload(self, event: str, component: str, aspect: str, severity: str, data: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            'ts': _now_iso(),
            'run_id': self._run_id,
            'event': event,
            'component': component,
            'aspect': aspect,
            'severity': severity,
            'data': self._redact_and_truncate(data or {}),
        }
        return payload

    def _writer_json(self, payload: Dict[str, Any]) -> None:
        try:
            line = json.dumps(payload, ensure_ascii=False)
        except Exception:
            # Fallback: stringify data
            safe = dict(payload)
            try:
                safe['data'] = _safe_str(payload.get('data'))
            except Exception:
                pass
            line = json.dumps(safe, ensure_ascii=False)
        try:
            with open(self._log_path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass
        if self._mirror and self._output:
            try:
                self._output.debug(line)
            except Exception:
                pass

    def _writer_text(self, payload: Dict[str, Any]) -> None:
        ts = payload.get('ts')
        comp = payload.get('component')
        asp = payload.get('aspect')
        ev = payload.get('event')
        sev = payload.get('severity')
        data = payload.get('data') or {}
        # Flatten one line with key=val previews
        pairs = []
        try:
            for k, v in (data.items() if isinstance(data, dict) else []):
                vv = v
                if isinstance(vv, (dict, list)):
                    try:
                        vv = json.dumps(vv, ensure_ascii=False)
                    except Exception:
                        vv = _safe_str(vv)
                pairs.append(f"{k}={vv}")
        except Exception:
            pass
        line = f"[{ts}] {comp} {asp}:{ev} " + (' '.join(pairs))
        try:
            with open(self._log_path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass
        if self._mirror and self._output:
            try:
                # Map severity to console level (best-effort)
                from utils.output_utils import OutputLevel
                lvl = OutputLevel.INFO
                if (sev or '').lower() == 'error':
                    lvl = OutputLevel.ERROR
                elif (sev or '').lower() == 'warning':
                    lvl = OutputLevel.WARNING
                elif (sev or '').lower() == 'debug':
                    lvl = OutputLevel.DEBUG
                self._output.write(line, level=lvl)
            except Exception:
                pass
