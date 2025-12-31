# Web and TUI notes

## Streaming behavior

Web/TUI streaming is MVP. When actions need input mid-stream, the server emits a terminal `done` SSE with a
`needs_interaction` token; the client resumes over JSON.

Some advanced, loop-heavy commands are CLI-only today (for example, `manage_chats`, `save_code`, examples
`debug_storage`). Web/TUI will display a warning if invoked.

## Web uploads and file handling

- Attach files via the upload button or drag-and-drop.
- Files are uploaded to a temp folder and immediately loaded into context.
- After loading, uploads are deleted; file contexts carry metadata:
  - `name`: original filename for display
  - `origin`: `upload`
  - `server_path`: absolute path where the file landed (ephemeral)
- Browsers do not expose local absolute paths; the assistant will not see your local path in Web mode.

## Options panel

Click the Options panel to set session params or tools options from a compact form. It includes presets for common keys
and autocompletes from `/api/params`.

## Development self-test (Web)

- Quick check: `python web/selftest.py` runs a headless e2e against the Starlette app using a fake session.
- Covers: `/api/status`, `/api/chat`, SSE `/api/stream`, action `needs_interaction` start -> resume, token reuse/expiry.
- No external services required.
