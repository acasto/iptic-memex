# Getting started

## Install

Requirements: Python 3.11+.

```bash
git clone https://github.com/acasto/iptic-memex.git
cd iptic-memex
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Using a virtual environment is recommended to avoid modifying your system Python.

This installs all dependencies for all AI providers. For customized installations (skip providers you don't need), see
"Custom installation" below.

## Requirements

- Python 3.11+
- pip

## Base requirements (all platforms)

These packages are required regardless of which AI providers you use:

```
click
Pygments
requests
bs4
beautifulsoup4
tiktoken
reportlab
trafilatura
pypdf
python-docx
openpyxl
pytz
```

## Platform-specific notes

### Windows
- `gnureadline` is not available and not needed on Windows
- `llama-cpp-python` may require additional setup. See https://github.com/abetlen/llama-cpp-python

### macOS
- All dependencies should install normally

### Linux
- All dependencies should install normally
- GPU acceleration may require additional system packages

## Provider-specific dependencies

You can exclude providers you don't plan to use to simplify installation:

### OpenAI
```
openai
```

### Anthropic (Claude)
```
anthropic
```

### Google
```
google-genai
```

### Cohere
```
cohere
```

### Local models (Llama, etc.)
```
llama-cpp-python
```

## Custom installation

### Example 1: Windows without local models

Create a custom `requirements.txt`:
```
openai
anthropic
google-genai
click
Pygments
requests
bs4
beautifulsoup4
tiktoken
reportlab
trafilatura
cohere
pypdf
python-docx
openpyxl
pytz
# llama-cpp-python - excluded for Windows simplicity
```

### Example 2: Minimal setup for OpenAI only
```
openai
click
Pygments
requests
bs4
beautifulsoup4
tiktoken
reportlab
trafilatura
pypdf
python-docx
openpyxl
pytz
gnureadline;platform_system=="Linux" or platform_system=="Darwin"
```

## Configuration

After installation:

1. Use the repo `config.ini` as a commented example and copy it to your user config:
   - `~/.config/iptic-memex/config.ini`
   - (The repo includes a `user-config` symlink pointing to this folder for convenience.)
2. Edit the user config to set API keys and preferred models (so updates to the repo don't overwrite your settings).
3. Models live in `models.ini` (also supported in `~/.config/iptic-memex/models.ini`).
4. Run `python main.py --help` to see available options.

Global settings:
- `config.ini` in the project directory
- `~/.config/iptic-memex/config.ini` for user-specific settings
- Or pass a custom config with `-c/--conf`

API keys:
- Put keys in the user `config.ini` (e.g., `api_key`) or via environment variables like `OPENAI_API_KEY`.

Per-turn prompts:
- Set `turn_prompt` in `config.ini` (DEFAULT/provider/model).
- It resolves via the prompt resolver and is templated (e.g., `{{turn:index}}`, `{{message_id}}`).
- Example:
  - `turn_prompt = message_id`
  - `prompts/message_id.txt`:
    `{{turn:index}} | id={{message_id}} | role={{turn:role}}`

## Run

```bash
python main.py chat
```

One-shot (completion) example:

```bash
echo "What is PI?" | python main.py -f -
```
