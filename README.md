# Iptic Memex

Iptic Memex is a command-line LLM workbench. It supports CLI chat, completion, agent, TUI, and Web modes, plus a flexible tool system, RAG, and optional hooks for sidecar analysis.

The name is a reference to the Memex, a device described by Vannevar Bush in his 1945 essay "As We May Think". See: https://en.wikipedia.org/wiki/Memex

![Iptic Memex demo](https://i.imgur.com/XLJ4AuY.gif)

---

## Highlights

- Multiple interaction modes: chat, completion, agent, TUI, and Web.
- Load and summarize local files (pdf/docx/xlsx/pptx/msg/audio/images) and web content.
- Built-in tools (file, cmd, websearch, ragsearch, memory, persona_review).
- Retrieval-Augmented Generation (RAG) with local indexes.
- Broad provider support (OpenAI, Anthropic, Gemini, OpenRouter, and more).
- Optional hooks for pre/post turn analysis and memory.
- Session persistence with autosave, resume, and checkpoints.

---

## Quickstart

Requirements: Python 3.11+.

```bash
git clone https://github.com/acasto/iptic-memex.git
cd iptic-memex
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set API keys in `config.ini` (or `~/.config/iptic-memex/config.ini`) or via env vars like `OPENAI_API_KEY`.

Run chat mode:

```bash
python main.py chat
```

One-shot (completion) example:

```bash
echo "What is PI?" | python main.py -f -
```

Tip: See `docs/getting-started.md` for platform-specific dependencies.

---

## Docs

Start here for the rest of the platform details:

- [Docs index](docs/README.md)
- [Getting started](docs/getting-started.md)
- [Modes](docs/modes.md)
- [CLI reference](docs/cli.md)
- [Tools](docs/tools.md)
- [Prompts](docs/prompts.md)
- [Templates](docs/templates.md)
- [Hooks](docs/hooks.md)
- [Runners](docs/runners.md)
- [Sessions](docs/sessions.md)
- [RAG](docs/rag.md)
- [MCP](docs/mcp.md)
- [Skills](docs/skills.md)
- [Sandbox and base-dir](docs/sandbox.md)
- [Agents](docs/agents.md)
- [Providers](docs/providers.md)
- [Logging](docs/logging.md)
- [Web and TUI notes](docs/web.md)
