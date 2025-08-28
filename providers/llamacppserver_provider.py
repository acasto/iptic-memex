import os
import json
import shlex
import socket
import subprocess
import time
import atexit
import secrets
from typing import Optional, Tuple, List

from providers.openai_provider import OpenAIProvider


class LlamaCppServerProvider(OpenAIProvider):
    """
    Managed llama.cpp OpenAI-compatible server provider.

    - Spawns `llama-server` with the configured GGUF model.
    - Disables the built-in Web UI via `--no-webui` by default.
    - Waits for readiness, then talks to it via the inherited OpenAI client.
    - Injects session-local prompt caching (cache_prompt=true) and disables
      official tool calling by default (pseudo tools only).
    - Supports speculative decoding by passing `-md <path>` when the model
      sets `draft_model_path = /abs/path/to/draft.gguf`.
    """

    # Signal to core that provider initialization may take noticeable time and
    # a user-facing indicator should be shown in chat-like UIs
    startup_wait_message = "Loading local model..."
    startup_ready_message = "Local model ready."

    def __init__(self, session):
        # Wrap the session with a view that injects our per-provider params
        session = self._SessionParamView(session)
        self._proc: Optional[subprocess.Popen] = None
        self._api_key: Optional[str] = None
        self._base_url: Optional[str] = None
        self._log_path: Optional[str] = None
        super().__init__(session)
        # Ensure child cleanup when process object exists
        atexit.register(self.cleanup)

    # ---- OpenAI client init override ---------------------------------
    def _initialize_client(self):  # type: ignore[override]
        params = self.session.get_params()

        binary = params.get('binary')
        if not binary:
            raise RuntimeError("[LlamaCppServer] 'binary' is required (path to llama-server)")
        binary = os.path.expanduser(str(binary))
        if not os.path.exists(binary):
            raise RuntimeError(f"llama-server not found at: {binary}")

        model_path = params.get('model_path')
        if not model_path:
            raise RuntimeError("Model config must include 'model_path' (GGUF file)")
        model_path = os.path.expanduser(str(model_path))
        if not os.path.exists(model_path):
            raise RuntimeError(f"model_path does not exist: {model_path}")

        host = str(params.get('host', '127.0.0.1')).strip() or '127.0.0.1'
        pr = str(params.get('port_range', '40100-40149')).strip()
        start_port, end_port = self._parse_port_range(pr)
        port = self._pick_free_port(host, start_port, end_port)
        if port is None:
            raise RuntimeError(f"No free port available in range {start_port}-{end_port}")

        # Build command line
        cmd: List[str] = [binary, '-m', model_path, '--host', host, '--port', str(port)]

        # Disable the Web UI by default
        # Verified from current llama-server help: `--no-webui` is supported and disables the UI.
        # This keeps the managed server headless and reduces noise / port conflicts.
        cmd.append('--no-webui')

        # Optional tuning from params
        ctx_size = params.get('context_size') or params.get('ctx_size')
        if ctx_size:
            cmd.extend(['-c', str(int(ctx_size))])
        ngl = params.get('n_gpu_layers')
        if ngl is not None and str(ngl).strip() != '':
            cmd.extend(['-ngl', str(int(ngl))])
        threads = params.get('threads')
        if threads:
            cmd.extend(['-t', str(int(threads))])
        cont_batching = params.get('cont_batching')
        if cont_batching is not None:
            tf = str(cont_batching).lower() not in ('false', '0', 'no')
            cmd.append('--cont-batching' if tf else '--no-cont-batching')
        parallel = params.get('parallel')
        if parallel:
            cmd.extend(['--parallel', str(int(parallel))])
        alias = params.get('alias')
        if alias:
            cmd.extend(['-a', str(alias)])
        chat_template = params.get('chat_template')
        if chat_template:
            cmd.extend(['--chat-template', str(chat_template)])

        # Speculative decoding (draft model)
        # If a model-level setting 'draft_model_path' is provided, pass it via '-md <path>'
        draft_model_path = params.get('draft_model_path')
        if isinstance(draft_model_path, str) and draft_model_path.strip():
            dmp = os.path.expanduser(draft_model_path.strip())
            if not os.path.exists(dmp):
                raise RuntimeError(f"draft_model_path does not exist: {dmp}")
            cmd.extend(['-md', dmp])

        # API key handling
        use_api_key = params.get('use_api_key', True)
        if isinstance(use_api_key, str):
            use_api_key = str(use_api_key).lower() not in ('false', '0', 'no')
        if host not in ('127.0.0.1', 'localhost') and not use_api_key:
            raise RuntimeError("Refusing to start llama-server on non-loopback without API key")
        if use_api_key:
            self._api_key = params.get('api_key') or secrets.token_hex(16)
            cmd.extend(['--api-key', self._api_key])

        # Extra server flags (advanced): support 'extra_flags', 'extra_flags_append', and legacy 'extra_server_args'
        def _parse_flags(val) -> list[str]:
            if not val:
                return []
            if isinstance(val, (list, tuple)):
                out: list[str] = []
                for item in val:
                    out.extend(_parse_flags(item))
                return out
            s = str(val).strip()
            if not s:
                return []
            try:
                return shlex.split(s)
            except Exception:
                return [s]

        extras: list[str] = []
        extras += _parse_flags(params.get('extra_flags'))
        extras += _parse_flags(params.get('extra_flags_append'))
        extras += _parse_flags(params.get('extra_server_args'))  # legacy alias
        if extras:
            cmd.extend(extras)

        # Logging: disabled by default. If 'log_path' or 'log_dir' provided, write there.
        # Otherwise, use DEVNULL to avoid PIPE backpressure.
        log_path = params.get('log_path')
        log_dir = params.get('log_dir')
        log_flag = params.get('log')
        enable_log = False
        self._log_path = None
        if log_path:
            self._log_path = os.path.expanduser(str(log_path))
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            enable_log = True
        elif log_dir:
            d = os.path.expanduser(str(log_dir))
            os.makedirs(d, exist_ok=True)
            self._log_path = os.path.join(d, f'llama-server-{port}.log')
            enable_log = True
        elif isinstance(log_flag, str) and str(log_flag).lower() in ('1', 'true', 'yes', 'on'):
            # If user explicitly enables 'log' without a path/dir, still discard to DEVNULL
            enable_log = False

        log_target = subprocess.DEVNULL
        self._log_handle = None  # type: ignore[attr-defined]
        if enable_log and self._log_path:
            self._log_handle = open(self._log_path, 'ab', buffering=0)
            log_target = self._log_handle

        # Spawn the server
        env = os.environ.copy()
        self._proc = subprocess.Popen(
            cmd,
            stdout=log_target,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )

        self._base_url = f"http://{host}:{port}/v1"
        timeout_s = float(params.get('startup_timeout', 90))
        self._wait_until_ready(host, port, timeout_s)

        # Initialize OpenAI client pointed at the local server
        from openai import OpenAI
        options = {
            'base_url': self._base_url,
            'timeout': params.get('timeout', 300),
        }
        if self._api_key:
            options['api_key'] = self._api_key
        else:
            # Some OpenAI SDK versions require a non-empty key; fall back to 'none'
            options['api_key'] = 'none'
        return OpenAI(**options)

    # ---- Utilities ----------------------------------------------------
    class _SessionParamView:
        """Wraps the session to inject provider-local params without mutating globals."""

        def __init__(self, base_session):
            self._s = base_session

        def __getattr__(self, item):
            if item == 'get_params':
                return self.get_params
            return getattr(self._s, item)

        def get_params(self):
            p = self._s.get_params()
            # Default to pseudo tools via config; allow users to override to 'official' when supported
            if not p.get('tool_mode'):
                p['tool_mode'] = 'pseudo'
            # Default stream_options to False (suppresses include_usage payload); allow override in config
            if 'stream_options' not in p:
                p['stream_options'] = False
            # Force vision off
            p['vision'] = False
            # Ensure extra_body.cache_prompt = true for session-local KV reuse
            eb = dict(p.get('extra_body') or {})
            eb['cache_prompt'] = True
            p['extra_body'] = eb
            return p

    @staticmethod
    def _parse_port_range(r: str) -> Tuple[int, int]:
        try:
            parts = [int(x) for x in str(r).split('-')]
            if len(parts) == 1:
                return parts[0], parts[0]
            if len(parts) >= 2:
                lo, hi = parts[0], parts[1]
                if lo > hi:
                    lo, hi = hi, lo
                return lo, hi
        except Exception:
            pass
        return 40100, 40149

    @staticmethod
    def _pick_free_port(host: str, start: int, end: int) -> Optional[int]:
        for port in range(start, end + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((host, port))
                    return port
                except OSError:
                    continue
        return None

    def _wait_until_ready(self, host: str, port: int, timeout_s: float) -> None:
        """Poll readiness endpoints until the server can serve completions.

        Stage 1: wait for HTTP service (/health or /v1/models) to be up.
        Stage 2: probe /v1/chat/completions to avoid 503 "Loading model" races.
        """
        import http.client
        start = time.time()
        # ---- Stage 1: server process ready ----
        while True:
            if self._proc and (self._proc.poll() is not None):
                raise RuntimeError(
                    f"llama-server exited early with code {self._proc.returncode}. "
                    f"Check logs at {self._log_path}"
                )
            try:
                conn = http.client.HTTPConnection(host, port, timeout=2.0)
                conn.request('GET', '/health')
                resp = conn.getresponse()
                if 200 <= resp.status < 300:
                    # Drain body to reuse connection pool cleanly
                    try:
                        resp.read()
                    except Exception:
                        pass
                    break
            except Exception:
                pass

            # Fallback probe: /v1/models
            try:
                conn = http.client.HTTPConnection(host, port, timeout=2.0)
                conn.request('GET', '/v1/models')
                resp = conn.getresponse()
                if 200 <= resp.status < 300:
                    try:
                        resp.read()
                    except Exception:
                        pass
                    break
            except Exception:
                pass

            if time.time() - start > timeout_s:
                raise TimeoutError(
                    f"Timed out waiting for llama-server on {host}:{port}. "
                    f"See log: {self._log_path}"
                )
            time.sleep(0.25)

        # ---- Stage 2: model loaded (avoid 503 Loading model) ----
        # Try to discover a model id for the request
        model_id = None
        try:
            conn = http.client.HTTPConnection(host, port, timeout=2.0)
            conn.request('GET', '/v1/models')
            resp = conn.getresponse()
            if 200 <= resp.status < 300:
                raw = resp.read()
                try:
                    data = json.loads(raw.decode('utf-8')) if raw else {}
                    arr = data.get('data') if isinstance(data, dict) else None
                    if isinstance(arr, list) and arr:
                        first = arr[0]
                        if isinstance(first, dict):
                            model_id = first.get('id') or first.get('name')
                except Exception:
                    pass
        except Exception:
            pass

        # Poll chat completions with a minimal request until it succeeds (or timeout)
        while True:
            if self._proc and (self._proc.poll() is not None):
                raise RuntimeError(
                    f"llama-server exited early with code {self._proc.returncode}. "
                    f"Check logs at {self._log_path}"
                )
            try:
                payload = {
                    'model': model_id or 'llama',
                    'messages': [{'role': 'user', 'content': '.'}],
                    'max_tokens': 1,
                    'stream': False,
                }
                body = json.dumps(payload)
                headers = {'Content-Type': 'application/json'}
                if self._api_key:
                    headers['Authorization'] = f'Bearer {self._api_key}'
                conn = http.client.HTTPConnection(host, port, timeout=5.0)
                conn.request('POST', '/v1/chat/completions', body=body, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                # Drain/close
                try:
                    resp.read()
                except Exception:
                    pass
                # 200 means model ready to serve
                if status == 200:
                    return
                # 503 while model loads -> keep waiting
                if status == 503:
                    # continue polling
                    pass
                else:
                    # Any other status: assume ready enough to proceed
                    return
            except Exception:
                # Ignore and retry until timeout
                pass

            if time.time() - start > timeout_s:
                raise TimeoutError(
                    f"Timed out waiting for model load on {host}:{port}. "
                    f"See log: {self._log_path}"
                )
            time.sleep(0.4)

    # ---- Cleanup ------------------------------------------------------
    def cleanup(self):
        try:
            if self._proc and (self._proc.poll() is None):
                # Graceful terminate, then kill if needed
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except Exception:
                    self._proc.kill()
            # Close log file if we opened one
            try:
                if getattr(self, '_log_handle', None) is not None:
                    self._log_handle.close()
                    self._log_handle = None
            except Exception:
                pass
        except Exception:
            pass

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
