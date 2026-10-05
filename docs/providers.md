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

Usage accounting includes cached and reasoning token subsets. Reasoning tokens
are already included in output tokens and are charged once. `price_cache_in`
defaults to `price_in`; set it for discounted cached input. The existing
`bill_reasoning_as_output` option is accepted for compatibility but no longer
adds or subtracts reasoning tokens. Costs accumulate at each request's configured
prices, so changing models does not reprice earlier calls; totals survive provider rebuilds between
these two providers.

## Anthropic and Google

Anthropic and Google providers are configured in `config.ini` and selected in `models.ini` the same way.
Tool calling behavior follows the `tool_mode` rules described below.

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
