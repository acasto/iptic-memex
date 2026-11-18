# Web and TUI chat

Start a local browser session with `python main.py web` or a terminal session with
`python main.py tui`. Both use the interactive model and project settings, tool
selection, write policy, hooks, input limits, and provider-call budget used by
CLI chat. Each session accepts one active operation. A second browser tab shares
the same conversation and approvals.

Use `--private` on either subcommand for a private session. Bare `--resume`
opens a picker inside the interface. Use `--resume --latest` to load the newest
session directly, or pass an ID or path. Web reloads the current conversation
from the server. See [Sessions](sessions.md) for persistence and privacy details.

## Input and cancellation

The TUI displays a modal when a tool requests input. Ctrl+S changes streaming
between runs. Ctrl+X cancels the active run. In Web, answer in the interaction
panel or use Stop. An approval continues the same run and action. Cancellation
is cooperative and can take time while a provider is blocked.

Workspace attachment commands are available in CLI chat. Workspace tasks and
schedules continue to use their own CLI entry points.

## Browser transport

The browser posts the message to `/api/stream/start` and opens the event stream
using the returned single-use token. The stream carries text deltas, completed
messages, tool activity, input requests, and the final outcome. The streaming
toggle controls provider streaming. Stop targets the active run in either setting.

`POST /api/chat` returns a JSON result. When the run needs input, JSON clients
receive a `state_token` and can post it with a response to `/api/action/resume`.
The same route handles standalone stepwise commands. Tokens expire after 15 minutes.
Pending tool approvals live in memory and end when the server shuts down.

An upload is staged in a temporary directory and returns an upload token. Sending
a question with that token loads the files in the owning run before its first
provider call. Sending files alone attaches them to the session for the next question.
The browser options panel reads a limited set of display settings from `/api/params`.

## Offline checks

`python web/selftest.py` exercises JSON chat, SSE, stepwise input, token reuse,
and expiry with an offline provider. Run `python -m pytest -q tests/web tests/tui`
for tool continuations, cancellation, uploads, browser route contracts, and
headless Textual interaction.
