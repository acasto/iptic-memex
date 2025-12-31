# Logging (observability)

Enable centralized logging in `config.ini` under `[LOG]` (off by default).

Defaults:
- JSONL format in `logs/` with `latest.log` symlink
- Sensitive fields redacted
- Previews truncated

Useful keys:
- `active = true|false`, `dir = logs`, `per_run = true|false`, optional `file` for a fixed path
- `format = json|text`, `mirror_to_console = true|false`
- Aspect toggles: `log_tool_use`, `log_cmd`, `log_provider`, `log_settings`, `log_errors`, `log_usage`,
  `log_messages`, `log_mcp`, `log_rag`, `log_web`

Adjust verbosity by aspect (e.g., `log_cmd = detail`) for deeper auditing.
