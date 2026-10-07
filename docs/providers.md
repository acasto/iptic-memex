# Providers

## Overview

Providers are configured in `config.ini`, while models live in `models.ini`. A model references a provider by name.

Memex supports:
- OpenAI, Anthropic, Google Gemini
- OpenAI-compatible APIs (OpenRouter, Perplexity, Groq, Mistral, DeepSeek, Cohere, Fireworks, Together, etc.)
- Local models via llama.cpp (in-process or managed server)
- Test/mocks (for development)

## Provider config basics

Provider sections live in `config.ini` (API keys, base URLs, provider-specific defaults).
Model sections live in `models.ini` (model name, context size, provider binding, optional overrides).

Example model entry:

```ini
[gpt-4o-mini]
provider = OpenAI
model_name = gpt-4o-mini
context_size = 128000
```

## OpenAI-compatible providers

Add a provider section in `config.ini`:

```ini
[my_openai_compat]
alias = OpenAI
base_url = https://api.example.com/v1
api_key = ${env:MY_API_KEY}
extra_body = { ... }  ; Optional, provider-specific parameters
```

Define models in `models.ini`:

```ini
[my_model_short_name]
provider = my_openai_compat
model_name = model-id-from-provider
context_size = 8192
response_label = My Model
extra_body = { ... }  ; Optional per-model settings
```

## OpenAI vs OpenAIResponses

Memex supports both OpenAI Chat Completions and Responses APIs:
- `OpenAI` provider: classic chat completions.
- `OpenAIResponses` provider: Responses API (typed events, function tools, optional state).

Use whichever matches your needs; set the provider on the model in `models.ini`.

`OpenAI` remains the general provider for compatible servers such as TensorFold
and vLLM. Keep `use_old_system_role` and `use_simple_message_format` for servers
that need them. Reasoning returned as either `reasoning_content` or `reasoning`
is saved with the field name used by that server and replayed on later turns.
Set `reasoning_field` to override that name when changing backends or loading an
older transcript. Simple message format still omits images.

Both providers accept `excluded_parameters` as a CSV string or list, regardless
of the `reasoning` flag. Exclusions also remove matching `extra_body` keys.
Use a dictionary in `extra_body` for backend-specific fields such as TensorFold's
`chat_template_kwargs`, `thinking_budget`, or sampling extensions. Neither
provider executes string-valued configuration as Python code.
New API fields that an older installed SDK does not recognize as keyword
arguments are forwarded through `extra_body` instead.

For Chat Completions, `max_completion_tokens`, `reasoning_effort`, `verbosity`
and `parallel_tool_calls` are forwarded when configured. An explicit
`max_completion_tokens` takes precedence over `max_tokens`; with `reasoning=true`,
legacy `max_tokens` is mapped to `max_completion_tokens`. `stream_options=false`
omits streaming usage options for servers that do not support them; a dictionary
can specify the options directly.

For Responses, the token-cap precedence is `max_output_tokens`,
`max_completion_tokens`, then `max_tokens`. `reasoning_effort` maps to
`reasoning.effort`, `reasoning_summary` maps to `reasoning.summary`, and `verbosity`
maps to `text.verbosity`. A dictionary in `reasoning` or `text` preserves other
native settings, including reasoning context and structured output format.
`temperature`, `top_p`, `include`, `metadata`, `parallel_tool_calls`, and cache
settings are also forwarded when present. Set `base_url` to the API root
(normally ending in `/v1`); the SDK adds `/responses` or `/chat/completions`.

Responses storage stays off by default. Memex saves native response output items
with each assistant turn and replays them, including encrypted reasoning,
assistant message phases, and function calls. These fields are part of saved
transcripts and checkpoints. With `vision=true`, images are sent as typed input
parts. With
`store=true` and `use_previous_response=true`, only new user turns and tool
results are sent after a matching, unchanged history prefix. Edited or trimmed
history and model changes fall back to local replay; clearing chat also clears
the provider's stored response ID. Chaining never resends a function call that
the previous response already contains.

Function calls with malformed arguments, token-limit truncation, or an unfinished
stream are blocked from execution. Refusals and Responses failure/incomplete
statuses are surfaced to the user. Both streaming implementations close the
upstream stream when cancelled. Responses strict tool schemas are normalized
recursively without changing the shared registry's definitions;
`nullable_optionals` retains its existing behavior for optional properties.

For Responses MCP pass-through, per-server `mcp_require_approval_<label>` takes
precedence over the global policy. Pending approvals use the UI confirmation
broker unless `mcp_auto_approve=true`; auto-approval does not enable storage.
Approvals are resolved on the next request and recorded for stateless replay.
CLI and TUI can prompt directly or through their broker; a UI without a broker
and a headless run report an explicit error instead of silently approving.

Usage accounting includes cached, cache-write, and reasoning token subsets.
Reasoning tokens are already included in output tokens and are charged once.
`price_cache_read` (or the existing `price_cache_in`) defaults to `price_in`;
set it for discounted cached input. `price_cache_write` also defaults to
`price_in`; `price_cache_write_5m` and `price_cache_write_1h` override that rate
when the actual write tier is known. All rates use the model's `price_unit`.
Cache reads and writes are subsets of input tokens, so write charges replace
ordinary input charges for those tokens rather than adding them twice.

For compatible APIs such as Kimi, both providers read `cache_write_tokens` from
the input usage details and use the `Msh-Usage-Cache-Write-Tokens-5m` / `1h`
response headers to identify the actual tiers. Usage exposes `turn_cache_writes`,
`total_cache_writes`, and their `_5m` / `_1h` subsets. If headers are unavailable,
writes use the generic `price_cache_write` rate; Memex does not infer a tier
from the requested TTL because an existing prefix can retain its original TTL.
See the [Kimi cache accounting contract](https://platform.kimi.ai/docs/guide/context-caching).

The existing
`bill_reasoning_as_output` option is accepted for compatibility but no longer
adds or subtracts reasoning tokens. Costs accumulate at each request's configured
prices, so changing models does not reprice earlier calls; totals survive provider rebuilds between
these two providers.

## Moonshot / Kimi

Moonshot uses the shared OpenAI-compatible provider; its former subclass has
been removed. Keep the provider name through an alias:

```ini
[Moonshot]
alias = OpenAI
base_url = https://api.moonshot.ai/v1
use_old_system_role = True
```

Models can continue to reference `provider = Moonshot`. Reasoning content is
preserved by the shared provider, including assistant tool-call messages.
Configure model-specific thinking controls and cache options with `extra_body`,
for example `{"prompt_cache_options": {"mode": "implicit", "ttl": "1h"}}`.
Set cache-write rates explicitly when they differ from ordinary input rates;
Memex does not assume a provider-specific multiplier. Use `excluded_parameters`
for sampling or reasoning settings that the selected model does not accept.
See the [Kimi model parameter reference](https://platform.kimi.ai/docs/api/models-overview).

## Anthropic

Configure `[Anthropic]` in `config.ini`, and select `provider = Anthropic` in
`models.ini`. `ANTHROPIC_API_KEY` is supported. `base_url`, client `timeout`,
`max_retries`, and `default_headers` remain configurable for compatible services.

The provider returns all visible text blocks and preserves native output blocks
in assistant transcript metadata. This includes signed and redacted thinking,
original client tool calls, and server/MCP tool results. Streaming reconstructs
the native message, including thinking signatures and final usage, for raw
inspection. Client calls retain both their API name and canonical dispatch name.

Native blocks are replayed when the assistant text, tool arguments, model,
system prompt, tools, thinking configuration, and preceding messages still
match the original request. Cache breakpoint placement may move without
invalidating replay. Edited or trimmed histories and incompatible configuration
changes fall back to text and normalized client-tool history. Transcript and
checkpoint reloads preserve native replay metadata; signatures are never edited
or fabricated. See [Claude thinking](https://platform.claude.com/docs/en/build-with-claude/thinking).

Native request controls include `thinking`, `output_config`, `tool_choice`,
`service_tier`, `container`, and `context_management`. Explicit native settings
win over convenience aliases: `reasoning_effort` maps to `output_config.effort`,
and `thinking_budget` maps to an enabled thinking configuration with that budget.
Choose a thinking configuration supported by the selected model; no model-name
heuristics enable thinking automatically. For example:

```ini
thinking = {"type": "adaptive"}
output_config = {"effort": "high"}
```

`tools` may contain additional native definitions (such as server tools); these
are combined with registry client tools in official mode. An explicitly configured
definition takes precedence when names collide. Use dictionary `extra_body` for
additional backend fields and `betas` (CSV/list) for beta headers. New body fields
unsupported by an older SDK are forwarded through its `extra_body` argument.
`excluded_parameters` (CSV/list) applies to native controls, aliases, and escape
hatch fields. Excluding `cache_control` also removes explicit cache breakpoints.
Dictionary/array configuration strings are parsed as JSON or Python literals,
never executed.

Prompt caching keeps explicit system/latest-message breakpoints by default,
including on tool results. Set `cache_strategy = automatic` to use the API's
moving top-level breakpoint instead. `prompt_cache_ttl = 5m|1h` controls generated
breakpoints; an explicitly supplied `cache_control` takes precedence. See
[Claude caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

Anthropic reports ordinary input, cache writes, and cache reads as separate
counts. `total_in`/`turn_in` include all three; `total_uncached_in`/`turn_uncached_in`
expose ordinary input. Costs accumulate at each request's configured prices and
survive provider rebuilds. `price_cache_write` and `price_cache_read` are explicit
rate names; legacy Anthropic `price_cache_in` and `price_cache_out` remain aliases
for **writes** and **reads**, respectively. `price_cache_write_1h` prices one-hour
writes separately (defaults to twice `price_in`). Configured token prices estimate
token charges; additional server-tool fees are not included. Raw native usage
retains server-tool counters for inspection.

Stop reasons distinguish completed replies, truncation, refusal, and server-tool
pauses. Malformed/non-object arguments and unfinished or truncated tool calls
cannot execute. Failed requests clear stale calls. Streams close on cancellation
and retain usage already observed. Server-tool `pause_turn` responses continue
automatically up to `max_server_continuations` additional requests (default 3;
set 0 to disable). Reaching the bound surfaces a pause notice and leaves the
native stop reason available for inspection. See
[Claude stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons).

Remote MCP remains gated by `[MCP].active`, including raw `extra_body` settings.
The default connector uses `mcp-client-2025-11-20`, with one `mcp_toolset` per
server and allowlists mapped into its tool configuration. Existing shared
`mcp_servers`, `mcp_headers_<label>`, and `mcp_allowed_<label>` settings still work.
Set `mcp_beta = legacy` (or `mcp-client-2025-04-04`) for the deprecated format;
other connector beta versions may be selected explicitly. Required beta headers
are retained even when using a compatible older SDK without a beta namespace.
See [Claude MCP migration](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector#migration-guide).

Image attachments are sent only with `vision = true`. `get_messages()` reflects
the assembled system/messages, including native blocks and tool results.

## Google

Configure `[Google]` in `config.ini` and select it in `models.ini`. This provider uses
the native `google-genai` SDK's `generateContent` API. Install `google-genai>=1.55.0`
from `requirements.txt`; `google-generativeai` is the older package and is not used
by this provider.

Authentication comes from `api_key`, `GOOGLE_API_KEY`, or `GEMINI_API_KEY`. Explicit
`vertexai`, `project`, and `location` options are forwarded to the SDK; when omitted,
the SDK can resolve its normal environment credentials and backend settings.
Client options include `base_url`, `api_version`, `default_headers`, `timeout`
(seconds), and `max_retries` (additional attempts). Native `http_options` is also
accepted as a dictionary or JSON/literal dictionary; its `timeout` is milliseconds
and `retry_options.attempts` counts the initial attempt. Native HTTP options take
precedence over the corresponding convenience options.

### Requests and thinking

Native `GenerateContentConfig` fields supported by the installed SDK can be set
directly. Common examples include `temperature`, `top_p`, `top_k`, `seed`,
`candidate_count`, `stop_sequences` (a list), `response_mime_type`,
`response_json_schema`, `response_schema`, and `thinking_config`. Structured values
can be dictionaries/lists or JSON/Python literal strings; they are never evaluated
as executable expressions.

Token caps use `max_output_tokens`, then `max_completion_tokens`, then `max_tokens`.
The native setting wins. Gemini's cap includes generated thinking tokens.

```ini
[my-gemini-model]
provider = Google
model_name = your-gemini-model
max_output_tokens = 8192
thinking_config = {"thinking_level": "low", "include_thoughts": true}
vision = True
```

Use `thinking_level` for Gemini 3 models and `thinking_budget` for Gemini 2.5 models.
The provider forwards explicit settings without inferring support from model names.
Convenience options `thinking_level`, `thinking_budget`, `include_thoughts`, and
`reasoning_effort` fill missing fields in `thinking_config`; `reasoning_effort` maps
to `thinking_level`. The legacy `reasoning` boolean does not override the model's
native thinking defaults. Available levels and budgets depend on the selected model.
Thought summaries are stored separately as `reasoning_content`, rather than being
mixed into the visible assistant answer.

`extra_body` supplies additional native wire fields. Its shape follows the REST API,
for example `{"generationConfig": {"seed": 42}}`. The same escape hatch is available
under `http_options.extra_body`; the latter wins on conflicts and nested dictionaries
are merged. `excluded_parameters` accepts a list or CSV and removes native fields
and their corresponding extra-body fields. Excluding a native token cap also blocks
its aliases. Required request fields such as model and contents should not be excluded.

### Tools, transcripts, and streaming

Official tool mode merges registry function declarations with configured native
`tools`, including supported built-in tools. Explicit function definitions win over
duplicate registry names. Full canonical schemas are copied into
`parameters_json_schema` without mutating the registry. Native `tool_config` wins
over `tool_choice`; the latter supports `auto`, `none`, `required`/`any`, or an
OpenAI-style function selector. Tool mode `none`/`pseudo` suppresses native tool
declarations, including those supplied through `extra_body`. The SDK's automatic
client function execution is disabled; TurnRunner executes registered client tools.

Native model parts, thought signatures, API function names, and function IDs survive
tool follow-ups and JSON checkpoints. Parallel results are grouped into one user
message with matching IDs. Native replay requires an unchanged prompt prefix, model,
backend, system instruction, tools, thinking settings, and assistant content. Edited,
trimmed, or imported tool histories are sent as labeled historical text with the
executed arguments and results, avoiding unsigned native calls that Gemini 3 rejects. Replacing
`contents` through `extra_body` disables native transcript replay for that response.

Streaming collects text and function calls from all chunks of the selected candidate,
retains signed empty-text parts, and merges cumulative usage. With multiple candidates,
only one candidate supplies the displayed answer and executable tools. Tool calls are
consumed once. Failed, blocked, cancelled, malformed, and unfinished responses cannot
execute client tools; token-limit calls are marked truncated. Vertex-style partial
function arguments are retained for inspection and rejected for execution, rather
than being treated as complete calls with empty arguments. Images are sent only when
`vision=True`, including in message introspection. Cancellation and cleanup close
HTTP responses and SDK client resources.

### Caching and accounting

Implicit caching is managed by Google's servers and can occur without an explicit
cache setting. `cached_content = cachedContents/<id>` refers to an existing explicit
cache; that cache owns its system instruction and tool declarations, so the provider
omits the automatically assembled system instruction and registry tools. Cache
creation, TTL changes, and deletion are managed separately. The old `endpoint`,
`prompt_caching`, and `cache_ttl` settings were unused and do not control implicit
caching.

Usage reports inclusive prompt counts, generated output (candidates plus thinking),
and separate `total_cached`/`turn_cached`, `total_reasoning`/`turn_reasoning`,
`total_candidates`/`turn_candidates`, and native tool-prompt counts. Streaming counts
are cumulative, not summed chunk by chunk. Unknown usage remains distinguishable via
`turn_usage_known`; interrupted streams retain any counts already observed.

Costs use `price_in`, `price_out`, and `price_unit`. Cached input uses
`price_cache_read`, falling back to `price_cache_in`, then the ordinary input rate.
Thinking tokens are charged at the output rate. Costs are accumulated at each
request's prices and restored across provider rebuilds; switching models does not
reprice earlier requests. These are token estimates using configured rates; explicit
cache storage, built-in tool fees, service-tier adjustments, and separate modality
rates are not automatically calculated.

See Google's [thinking/signature guidance](https://ai.google.dev/gemini-api/docs/generate-content/thinking)
and [context caching documentation](https://ai.google.dev/gemini-api/docs/generate-content/caching)
for model-specific behavior.

## Local models: llama.cpp

Two options:

1) **LlamaCpp** (in-process):
   - Provider: `LlamaCpp`
   - Requires `llama-cpp-python` and a local GGUF model path.

2) **LlamaCppServer** (managed server):
   - Provider: `LlamaCppServer`
   - Spawns `llama-server` and connects via the OpenAI-compatible API.
   - On POSIX, isolates the server from terminal Ctrl+C and stops it explicitly during session cleanup.

Minimal model example for LlamaCppServer:

```ini
[local-llama]
provider = LlamaCppServer
model_path = /abs/path/to/model.gguf
stream = true
tools = false
```

Required provider config (config.ini):

```ini
[LlamaCppServer]
binary = /abs/path/to/llama-server
```

Optional settings:
- `host`, `port_range`, `startup_timeout`
- `use_api_key` (default true)
- `log_path` or `log_dir`
- `extra_flags` / `extra_flags_append`
- `draft_model_path` (speculative decoding)

## Tool calling modes

Global default: `[TOOLS].tool_mode` (official|pseudo).

Overrides:
- Per provider: `[Provider].tool_mode`
- Per model: `[Model].tool_mode`

Notes:
- LlamaCpp defaults to `pseudo` tools (official tools are not reliable).
- LlamaCppServer defaults to `pseudo` unless overridden.
- Some managed providers (e.g., GPT-OSS) default to official tools.

## Compatibility flags (OpenAI-compatible)

Some OpenAI-compatible providers require legacy message formatting. Two common flags are supported per provider or per
model:

- `use_old_system_role = True` - send system prompt with role `system` instead of `developer`.
- `use_simple_message_format = True` - use a simple `{role, content: string}` format instead of the modern content array.

These can be set in `config.ini` provider sections or in `models.ini` per model.

## API keys

Set keys in `config.ini` or via environment variables (e.g., `OPENAI_API_KEY`). The config supports `${env:VAR}` for
interpolation.
