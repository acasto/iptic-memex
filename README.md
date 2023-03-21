# Description

A straight-forward CLI program in Python for interfacing LLM providers through their APIs. Currently tested OpenAI
but designed to be easily extensible to other providers and interactions. Input can be piped in from the command line
or entered interactively through 'ask' and 'chat' modes. Chat mode features the ability to save conversations in a
human-readable conversation format with custom extension for use with external applications such as Obsidian. Options 
can be set through both configuration files (configparser) and the command line (Click).

This is still very much a work in progress but somewhat useful. I'm still learning.

# Features

- [x] Flexible & extensible configuration & CLI
- [x] Load custom prompts from files
- [x] Save conversations in human-readable format
- [x] Load saved conversations back into chat mode
- [x] Supports streaming in both completion and chat

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

