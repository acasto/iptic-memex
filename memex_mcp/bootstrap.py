from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any, Dict, Tuple
import time


def _coerce_bool(val: Any, default: bool = False) -> bool:
    try:
        if isinstance(val, bool):
            return val
        s = str(val).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    except Exception:
        pass
    return default


def _parse_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    try:
        s = str(val)
    except Exception:
        return []
    items = [x.strip() for x in s.split(',')]
    return [x for x in items if x]


def _expand_env_in_headers(obj: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (obj or {}).items():
        if isinstance(v, str) and v.startswith('${env:') and v.endswith('}'):
            name = v[6:-1]
            out[k] = os.environ.get(name, '')
        else:
            out[k] = v
    return out


def _load_headers_from_command(cmd: str) -> Dict[str, str]:
    try:
        # Run via shell to allow simple && usage as in examples
        proc = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        text = proc.stdout.strip() or proc.stderr.strip()
        data = json.loads(text)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _get_cfg_sections(session) -> Tuple[dict, dict[str, dict]]:
    base = getattr(getattr(session, 'config', None), 'base_config', None)
    if base is None:
        return {}, {}
    # Access raw sections dict safely
    try:
        sec = getattr(base, '_sections', {}).get('MCP', {}) or {}
    except Exception:
        sec = {}
    # Collect per-server subsections
    servers: dict[str, dict] = {}
    try:
        for key, val in getattr(base, '_sections', {}).items():
            if key.startswith('MCP.') and isinstance(val, dict):
                name = key.split('.', 1)[1]
                servers[name] = val
    except Exception:
        pass
    return sec, servers


def _normalize_server_entry(name: str, raw: dict) -> dict:
    # ConfigParser stores keys lowercased
    tr = (raw.get('transport') or '').strip().lower()
    url = raw.get('url')
    cmd = raw.get('command') or raw.get('cmd')
    headers_raw = raw.get('headers')
    headers_cmd = raw.get('headers_command')
    allowed = raw.get('allowed_tools')
    approve = raw.get('require_approval')
    autoload = _coerce_bool(raw.get('autoload'), False)
    auto_register = _coerce_bool(raw.get('auto_register'), None)  # None means inherit global
    auto_alias = _coerce_bool(raw.get('auto_alias'), None)

    # Headers can be JSON or a dict-like string; prefer JSON
    headers: Dict[str, str] = {}
    if isinstance(headers_raw, dict):
        headers = {str(k): str(v) for k, v in headers_raw.items()}
    elif isinstance(headers_raw, str) and headers_raw.strip():
        s = headers_raw.strip()
        try:
            if s.startswith('{'):
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    headers = {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            pass
    headers = _expand_env_in_headers(headers)
    if isinstance(headers_cmd, str) and headers_cmd.strip():
        cmd_headers = _load_headers_from_command(headers_cmd)
        headers.update(cmd_headers)

    # Normalize transport: allow 'provider'|'http'|'stdio'; infer when missing
    if tr not in ('provider', 'http', 'stdio'):
        tr = 'http' if url else 'stdio'

    return {
        'name': name,
        'transport': tr,
        'url': url,
        'command': cmd,
        'headers': headers,
        'allowed_tools': _parse_list(allowed),
        'require_approval': (approve or '').strip().lower() if isinstance(approve, str) else None,
        'autoload': autoload,
        'auto_register': auto_register,
        'auto_alias': auto_alias,
    }


def _apply_provider_passthrough(session, servers: dict[str, dict]) -> None:
    """Merge MCP server details into provider params for pass-through providers.

    Note: We no longer use provider-local 'enable_builtin_tools'. Gating is
    centralized on [MCP].active and provider capability.
    """
    # mcp_servers mapping "name=url"
    mapping: dict[str, str] = {}
    try:
        existing = session.get_params().get('mcp_servers')
        if isinstance(existing, dict):
            mapping.update({str(k): str(v) for k, v in existing.items()})
        elif isinstance(existing, str) and existing.strip():
            for item in existing.split(','):
                if '=' in item:
                    k, v = item.split('=', 1)
                    mapping[k.strip()] = v.strip()
    except Exception:
        pass
    for name, s in servers.items():
        if s.get('url'):
            mapping[name] = s['url']
    if mapping:
        csv_val = ', '.join(f"{k}={v}" for k, v in mapping.items())
        session.config.set_option('mcp_servers', csv_val)

    # headers/allowed/approval per server
    for name, s in servers.items():
        if s.get('headers'):
            try:
                session.config.set_option(f'mcp_headers_{name}', json.dumps(s['headers']))
            except Exception:
                pass
        if s.get('allowed_tools'):
            session.config.set_option(f'mcp_allowed_{name}', ','.join(s['allowed_tools']))
        ra = s.get('require_approval')
        if ra in ('always', 'never'):
            session.config.set_option(f'mcp_require_approval_{name}', ra)


def autoload_mcp(session) -> None:
    """Bootstrap MCP based on [MCP] config: autoload connections and provider pass-through.

    No-op unless [MCP].active = true.
    """
    global_sec, raw_servers = _get_cfg_sections(session)
    if not global_sec:
        return
    # Respect session override for [MCP].active when present
    try:
        override_active = session.get_option('MCP', 'active', fallback=None)
    except Exception:
        override_active = None
    if override_active is not None:
        try:
            active_val = _coerce_bool(override_active, False)
        except Exception:
            active_val = False
    else:
        active_val = _coerce_bool(global_sec.get('active'), False)
    if not active_val:
        return

    # Non-interactive gating: only enable MCP when [AGENT].use_mcp is true
    # Non-interactive = Agent mode or Completion mode
    non_interactive = False
    try:
        non_interactive = bool(getattr(session, 'in_agent_mode', lambda: False)() or session.get_flag('completion_mode', False))
    except Exception:
        non_interactive = False
    if non_interactive:
        try:
            use_mcp = bool(session.get_option('AGENT', 'use_mcp', fallback=False))
        except Exception:
            use_mcp = False
        if not use_mcp:
            return
        # Optional startup delay to allow managed servers to initialize quietly
        try:
            sd = session.get_option('MCP', 'startup_delay', fallback=None)
            delay_s = None
            if sd is not None:
                s = str(sd).strip().lower()
                if s.endswith('ms'):
                    delay_s = float(s[:-2]) / 1000.0
                elif s.endswith('s'):
                    delay_s = float(s[:-1])
                elif s.endswith('m'):
                    delay_s = float(s[:-1]) * 60.0
                else:
                    delay_s = float(s)
            if delay_s and delay_s > 0:
                time.sleep(delay_s)
        except Exception:
            pass

    # Set global client knobs into session overrides for MCPClient
    for key in (
        'use_sdk', 'connect_timeout', 'call_timeout', 'retries',
        'allowed_resource_schemes', 'max_resource_bytes',
        'debug', 'http_fallback',
    ):
        if key in global_sec:
            try:
                session.config.set_option(key, global_sec[key])
            except Exception:
                pass

    # Resolve registry: prefer explicit [MCP].mcp_servers list, else all defined subsections
    known = _parse_list(global_sec.get('mcp_servers'))
    servers: dict[str, dict] = {}
    if known:
        for name in known:
            if name in raw_servers:
                servers[name] = _normalize_server_entry(name, raw_servers[name])
    else:
        for name, sec in raw_servers.items():
            servers[name] = _normalize_server_entry(name, sec)

    # Merge per-server autoload flag into the autoload list
    autoload_items = _parse_list(global_sec.get('autoload'))
    for name, s in servers.items():
        if s.get('autoload') and name not in [x.split(':', 1)[0] for x in autoload_items]:
            autoload_items.append(name)

    # If in non-interactive and [AGENT].available_mcp is set, restrict autoload set
    if non_interactive:
        try:
            avail = [x.strip() for x in str(session.get_option('AGENT', 'available_mcp', fallback='') or '').split(',') if x.strip()]
        except Exception:
            avail = []
        if avail:
            autoload_items = [x for x in autoload_items if (str(x).split(':', 1)[0].strip() in set(avail))]

    if not autoload_items:
        return

    # Build app-side and provider target sets based on per-server transport
    app_http: dict[str, dict] = {}
    app_stdio: dict[str, dict] = {}
    provider_targets: dict[str, dict] = {}

    for item in autoload_items:
        name = str(item).split(':', 1)[0].strip()
        s = servers.get(name)
        if not s:
            continue
        mode = s.get('transport')
        if mode == 'provider':
            provider_targets[name] = s
        elif mode == 'http':
            app_http[name] = s
        elif mode == 'stdio':
            app_stdio[name] = s

    # Log autoload plan (labels only; no secrets)
    try:
        plan = {
            'http': list(app_http.keys()),
            'stdio': list(app_stdio.keys()),
            'provider': list(provider_targets.keys()),
        }
        session.utils.logger.mcp_event('autoload_plan', plan, component='mcp.bootstrap')
    except Exception:
        pass

    # Provider pass-through synthesis (only if provider supports and [MCP] is active)
    prov = getattr(session, 'get_provider', lambda: None)()
    try:
        supports = bool(getattr(prov, 'supports_mcp_passthrough', lambda: False)())
    except Exception:
        supports = False
    try:
        mcp_active = bool(session.get_option('MCP', 'active', fallback=False))
    except Exception:
        mcp_active = False
    if provider_targets and supports and mcp_active:
        _apply_provider_passthrough(session, provider_targets)
        try:
            session.utils.logger.mcp_event('provider_passthrough', {'targets': list(provider_targets.keys())}, component='mcp.bootstrap')
        except Exception:
            pass

    # App-side connections
    from memex_mcp.client import get_or_create_client
    client = get_or_create_client(session)
    for name, s in app_http.items():
        try:
            client.connect_http(name, s.get('url') or '', headers=s.get('headers') or {})
            try:
                session.utils.logger.mcp_event('connect_http', {'name': name}, component='mcp.bootstrap')
            except Exception:
                pass
        except Exception:
            continue
    for name, s in app_stdio.items():
        try:
            client.connect_stdio(name, s.get('command') or '')
            try:
                session.utils.logger.mcp_event('connect_stdio', {'name': name}, component='mcp.bootstrap')
            except Exception:
                pass
        except Exception:
            continue

    # Optional: auto-register tools discovered on app-side connections (http & stdio)
    # Compute alias preference (global default true; per-server override)
    try:
        global_auto_alias = _coerce_bool(global_sec.get('auto_alias'), True)
    except Exception:
        global_auto_alias = True

    # Record autoload summary for UI commands
    try:
        summary = session.get_user_data('__mcp_autoload__') or {}
        for name, s in list(app_http.items()) + list(app_stdio.items()):
            try:
                sa = s.get('auto_alias')
                do_alias = global_auto_alias if sa is None else bool(sa)
                summary[name] = {'autoload': True, 'alias': do_alias}
            except Exception:
                summary[name] = {'autoload': True, 'alias': True}
        session.set_user_data('__mcp_autoload__', summary)
    except Exception:
        pass

    # Register tools for all autoloaded app-side connections
    try:
        registrar = session.get_action('mcp_register_tools')
    except Exception:
        registrar = None
    if registrar:
        for name, s in list(app_http.items()) + list(app_stdio.items()):
            try:
                sa = s.get('auto_alias')
                do_alias = global_auto_alias if sa is None else bool(sa)
                args = [name]
                # If per-server allowed_tools are set, restrict registration
                try:
                    allow_list = list(s.get('allowed_tools') or [])
                except Exception:
                    allow_list = []
                if allow_list:
                    args.append('--tools=' + ','.join(allow_list))
                if do_alias:
                    args.append('--alias')
                registrar.run(args)
                try:
                    session.utils.logger.mcp_event('register_tools', {'name': name, 'alias': do_alias, 'restricted': bool(allow_list)}, component='mcp.bootstrap')
                except Exception:
                    pass
            except Exception:
                continue
