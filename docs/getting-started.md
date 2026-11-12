# Getting started

## Install with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/acasto/iptic-memex.git
cd iptic-memex
uv sync --locked
uv run --locked python main.py --help
```

The checkout selects Python 3.11 in `.python-version`; the application supports
Python 3.11+. uv can provision the selected interpreter if it is missing. Packages
are installed in `.venv`, without changing global Python packages. The initial
installation includes the Python provider dependencies, Web, and TUI support.

`pyproject.toml` declares dependencies and `uv.lock` records resolved versions.
`--locked` fails if the declaration and lock disagree rather than silently changing
the lock. Ordinary sync/run commands do not request dependency upgrades.

## Install with pip instead

uv is optional. With Python 3.11 installed:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
python main.py --help
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell.
`requirements.txt` is a generated, pinned export of the runtime dependencies in
`uv.lock`, including artifact hashes. It excludes development dependencies. Neither
this file nor the lockfile pins operating-system build tools.

## Platform and provider requirements

- `LlamaCpp` uses the published `llama-cpp-python` package in the project environment.
  Building it may require a C/C++ toolchain (for example, Xcode command-line tools
  on macOS). GPU build options are environment-specific; see the
  [bindings installation instructions](https://github.com/abetlen/llama-cpp-python#installation).
  Supply any desired build settings when syncing/installing the environment.
- `LlamaCppServer` launches your separately installed `llama-server` using the
  configured `binary` path. Installing memex does not install or update that binary.
- MLX and MLX-LM are installed on Apple Silicon macOS only. Other platforms skip
  them; the remaining providers are unaffected.
- `gnureadline` is installed on macOS/Linux and skipped on Windows.
- Optional external integrations such as MarkItDown retain their own setup;
  see [Tools](tools.md).

## Shell wrappers

On macOS/Linux, after installing `.venv`, use `scripts/memex`, `scripts/chat`, or
`scripts/ask "your question"`. Add the checkout's `scripts` directory to PATH or
symlink the wrappers into an existing PATH directory. They find the checkout and
use its `.venv/bin/python`; no activation or uv installation is needed at runtime.
They preserve your working directory, including for project discovery, attachments,
and the tool sandbox. After pulling dependency updates, run `uv sync --locked`
(or reinstall the exported requirements with pip) before using the wrappers.

## Development and dependency updates

`uv sync --locked` also installs the `dev` group. Run tests with `make test` or
`uv run --locked python -m pytest -q`. A pip development environment can install
pytest separately and use `make test PYTEST="python -m pytest"`.

To update one dependency deliberately:

```bash
uv lock --upgrade-package openai
uv sync --locked
make test
make export-requirements
```

Review and commit changes to `pyproject.toml` (when applicable), `uv.lock`, and
`requirements.txt` together. Use `uv add PACKAGE` to declare a new dependency or
`uv add --dev PACKAGE` for development tools. `uv lock --upgrade` requests a full
upgrade; it is not required for normal setup or execution.

Do not edit `requirements.txt` manually. Regenerate it with:

```bash
uv export --locked --no-dev --no-emit-project --output-file requirements.txt
```

## Configuration

For a pip installation, activate `.venv` and replace `uv run --locked python`
with `python` in the examples below.

After installation:

1. Use the repo `config.ini` as a commented example and copy it to your user config:
   - `~/.config/iptic-memex/config.ini`
   - (The repo includes a `user-config` symlink pointing to this folder for convenience.)
2. Edit the user config to set API keys and preferred models (so updates to the repo don't overwrite your settings).
3. Models live in `models.ini` (also supported in `~/.config/iptic-memex/models.ini`).
4. Run `uv run --locked python main.py --help` to see available options.

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
uv run --locked python main.py chat
```

One-shot (completion) example:

```bash
echo "What is PI?" | uv run --locked python main.py -f -
```
