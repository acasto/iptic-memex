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
