from __future__ import annotations

import contextlib
import contextvars
import json
import os
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _new_id(n: int = 16) -> str:
    try:
        return uuid.uuid4().hex[:n]
    except Exception:
        return "0" * n


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

    def __init__(self, config, output_handler=None, *, base_context: Optional[dict] = None) -> None:
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
        self._log_dir = None
        self._rotate: str = (self._get('rotation', 'size') or 'size').strip().lower()
        self._max_bytes: int = int(self._get('max_bytes', 5_000_000) or 5_000_000)
        self._backup_count: int = int(self._get('backup_count', 10) or 10)
        self._max_age_days: int = int(self._get('max_age_days', 0) or 0)
        self._current_day = datetime.utcnow().strftime('%Y%m%d')
        if self._active:
            self._log_path = self._open_logfile()
        self._write = self._writer_json if self._format == 'json' else self._writer_text
        # Correlation context (per logger instance; safe for async via ContextVar)
        self._base_context: Dict[str, Any] = dict(base_context or {})
        self._ctx_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
            f"memex_log_ctx_{id(self)}",
            default={},
        )

    # --- Public helpers -------------------------------------------------
    def active(self) -> bool:
        return bool(self._active and self._log_path)

    def set_base_context(self, ctx: Optional[dict]) -> None:
        self._base_context = dict(ctx or {})

    def update_base_context(self, ctx: Optional[dict]) -> None:
        if not ctx:
            return
        try:
            self._base_context.update({k: v for k, v in (ctx or {}).items() if v is not None})
        except Exception:
            return

    def get_context(self) -> Dict[str, Any]:
        try:
            current = self._ctx_var.get()
        except Exception:
            current = {}
        out: Dict[str, Any] = {}
        try:
            out.update(self._base_context or {})
        except Exception:
            pass
        try:
            out.update(current or {})
        except Exception:
            pass
        return out

    @contextlib.contextmanager
    def context(self, **ctx):
        """Temporarily add/override correlation fields for all emitted events."""
        try:
            cur = dict(self._ctx_var.get() or {})
        except Exception:
            cur = {}
        nxt = dict(cur)
        for k, v in (ctx or {}).items():
            if v is None:
                nxt.pop(k, None)
            else:
                nxt[k] = v
        token = None
        try:
            token = self._ctx_var.set(nxt)
            yield nxt
        finally:
            if token is not None:
                try:
                    self._ctx_var.reset(token)
                except Exception:
                    pass

    @contextlib.contextmanager
    def span(self, kind: str, **ctx):
        """Create a new span nested under the current span, if any."""
        parent = None
        if "parent_span_id" in ctx:
            # Pop to avoid passing parent_span_id twice to context().
            parent = ctx.pop("parent_span_id", None)
        else:
            try:
                parent = self.get_context().get('span_id')
            except Exception:
                parent = None
        span_id = ctx.pop("span_id", None) or _new_id(16)
        with self.context(span_id=span_id, parent_span_id=parent, span_kind=kind, **ctx) as merged:
            yield merged

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
                path = os.path.join(log_dir, 'memex.log')

            try:
                self._log_dir = os.path.dirname(path) or log_dir
            except Exception:
                self._log_dir = log_dir

            # Ensure parent dir exists for the file (in case explicit includes subfolders)
            os.makedirs(os.path.dirname(path) or log_dir, exist_ok=True)

            # Touch file
            with open(path, 'a', encoding='utf-8'):
                pass
            return path
        except Exception:
            return None

    def _split_ext(self, path: str) -> tuple[str, str]:
        try:
            root, ext = os.path.splitext(path)
            return root, ext
        except Exception:
            return path, ''

    def _list_rotated_files(self) -> list[str]:
        """Return log files in log_dir that share the base name (excluding the current file)."""
        if not self._log_path:
            return []
        try:
            log_dir = self._log_dir or os.path.dirname(self._log_path)
            root, ext = self._split_ext(self._log_path)
            base_root = os.path.basename(root)
            base = os.path.basename(self._log_path)
            items = []
            for name in os.listdir(log_dir):
                if name == base:
                    continue
                if ext and not name.endswith(ext):
                    continue
                if name.startswith(base_root + ".") or name.startswith(base_root + "-"):
                    items.append(os.path.join(log_dir, name))
            return items
        except Exception:
            return []

    def _prune_by_age(self) -> None:
        """Remove rotated files older than max_age_days (best-effort)."""
        if not self._max_age_days or self._max_age_days <= 0:
            return
        try:
            cutoff = time.time() - (float(self._max_age_days) * 86400.0)
        except Exception:
            return
        for path in self._list_rotated_files():
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except Exception:
                continue

    def _rotate_size(self) -> None:
        if not self._log_path or not self._backup_count or self._backup_count <= 0:
            return
        base = self._log_path
        root, ext = self._split_ext(base)
        # Remove oldest
        try:
            oldest = f"{root}.{self._backup_count}{ext}"
            if os.path.exists(oldest):
                os.remove(oldest)
        except Exception:
            pass
        # Shift down
        for i in range(self._backup_count - 1, 0, -1):
            try:
                src = f"{root}.{i}{ext}"
                dst = f"{root}.{i + 1}{ext}"
                if os.path.exists(src):
                    os.replace(src, dst)
            except Exception:
                continue
        # Rotate current to .1
        try:
            if os.path.exists(base):
                os.replace(base, f"{root}.1{ext}")
        except Exception:
            pass
        try:
            with open(base, 'a', encoding='utf-8'):
                pass
        except Exception:
            pass
        self._prune_by_age()

    def _rotate_daily(self, today: str) -> None:
        if not self._log_path:
            return
        base = self._log_path
        root, ext = self._split_ext(base)
        # Rotate current file into a dated name for the previous day.
        dated = f"{root}-{self._current_day}{ext}"
        # Avoid collisions by appending .N
        try:
            if os.path.exists(dated):
                n = 1
                while True:
                    cand = f"{root}-{self._current_day}.{n}{ext}"
                    if not os.path.exists(cand):
                        dated = cand
                        break
                    n += 1
        except Exception:
            pass
        try:
            if os.path.exists(base) and os.path.getsize(base) > 0:
                os.replace(base, dated)
        except Exception:
            pass
        try:
            with open(base, 'a', encoding='utf-8'):
                pass
        except Exception:
            pass
        self._current_day = today
        self._prune_by_age()

    def _maybe_rotate(self, append_bytes: int = 0) -> None:
        if not self._active or not self._log_path:
            return
        rotate = (self._rotate or 'off').strip().lower()
        if rotate in ('off', 'none', 'false', '0'):
            return
        today = datetime.utcnow().strftime('%Y%m%d')
        try:
            if 'daily' in rotate and today != self._current_day:
                self._rotate_daily(today)
        except Exception:
            pass
        try:
            if 'size' in rotate and self._max_bytes and self._max_bytes > 0:
                try:
                    current = os.path.getsize(self._log_path)
                except Exception:
                    current = 0
                if (current + int(append_bytes or 0)) >= int(self._max_bytes):
                    self._rotate_size()
        except Exception:
            pass

    def _append_line(self, line: str) -> None:
        if not self._log_path:
            return
        try:
            self._maybe_rotate(len(line.encode('utf-8', errors='ignore')) + 1)
        except Exception:
            pass
        try:
            with open(self._log_path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass

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
        try:
            ctx = self.get_context()
        except Exception:
            ctx = {}
        payload = {
            'ts': _now_iso(),
            'run_id': self._run_id,
            'event': event,
            'component': component,
            'aspect': aspect,
            'severity': severity,
            'ctx': self._redact_and_truncate(ctx or {}),
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
        self._append_line(line)
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
        self._append_line(line)
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
