# Logging (observability)

Enable centralized logging in `config.ini` under `[LOG]` (off by default).

Defaults:
- JSONL format in `logs/` with rotation (single active file + rotated history)
- Sensitive fields redacted
- Previews truncated

Useful keys:
- `active = true|false`, `dir = logs`, `file = memex.log`
- Rotation: `rotation = off|size|daily|daily,size`, plus `max_bytes`, `backup_count`, `max_age_days`
- `format = json|text`, `mirror_to_console = true|false`
- Aspect toggles: `log_tool_use`, `log_cmd`, `log_provider`, `log_settings`, `log_errors`, `log_usage`,
  `log_messages`, `log_mcp`, `log_rag`, `log_web`

Adjust verbosity by aspect (e.g., `log_cmd = detail`) for deeper auditing.
