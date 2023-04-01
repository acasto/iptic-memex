# Description

Iptic Memex is a Python program that offers a straight-forward CLI interface for interacting with LLM providers through
their APIs. Currently tested with OpenAI but designed to be easily extensible to other providers and interactions. Input
can be piped in from the command line or entered interactively through 'ask' and 'chat' modes. Chat mode features the
ability to save conversations in a human-readable conversation format with a configurable extension for use with external
applications such as Obsidian.

The name is a reference to the Memex, a device described by Vannevar Bush in his 1945 essay "As We May Think" which he
envisioned a device that would compress and store all of their knowledge. https://en.wikipedia.org/wiki/Memex

# Features

- [x] CLI (command line) mode suitable for quick questions or scripting
- [x] Chat mode for longer conversations
- [x] Ask mode for single questions 
- [x] Can load in files in both chat and ask modes
- [x] Load custom prompts from files including stdin
- [x] Run completions on files or stdin
- [x] Save conversations in human-readable format
- [x] Load saved conversations back into chat mode
- [x] Supports streaming in both completion and chat
- [x] Supports syntax highlighting in interactive modes

# Installation & Usage

- Just clone the repo and install the requirements. Then run the program with `python main.py`.
- Configuration can be done through
  - `config.ini` in the project directory
  - `~/.config/iptic-memex/config.ini` in the user directory
  - via custom .ini file with `-c` or `--conf` flag
- API key can be set in the config file as `api_key` or via environment variable `OPENAI_API_KEY`.
- Usage is well documented with click and can be accessed with `python main.py --help`.
- From within chat mode you can access the help menu with `help` or `?`. 

# License

This project is licensed under the terms of the MIT license. 

