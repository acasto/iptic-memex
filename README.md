# Description

Iptic Memex is a Python program that offers a straight-forward CLI interface for interacting with LLM providers through
their APIs. Currently tested with OpenAI, Anthropic, and Google Gemini. Input can be piped in from the command line or entered interactively 
through 'ask' and 'chat' modes. Chat mode features the ability to save conversations in a human-readable conversation format 
with a configurable extension for use with external applications such as Obsidian.

The name is a reference to the Memex, a device described by Vannevar Bush in his 1945 essay "As We May Think" which he
envisioned a device that would compress and store all of their knowledge. https://en.wikipedia.org/wiki/Memex

![Imgur Image](https://i.imgur.com/XLJ4AuY.gif)

# Features

- [x] CLI (command line) mode suitable for quick questions or scripting
- [x] Chat mode for longer conversations
- [x] Ask mode for single questions 
- [x] Can load in files in both chat and ask modes
- [x] Can load in text scraped from a URL in both chat and ask modes
- [x] Select text to scrape by ID or Class
- [x] Tracks token usage in chat mode with tiktoken 
- [x] Load custom prompts from files including stdin
- [x] Run completions on files, URLs, or stdin
- [x] Save conversations in human-readable format
- [x] Load saved conversations back into chat mode
- [x] Supports streaming in both completion and chat
- [x] Supports syntax highlighting in interactive modes
- [x] Cross-platform support (tested on Windows and Linux)

# Installation & Usage

### Basic Usage

- Clone the repository
`git clone https://github.com/acasto/iptic-memex.git`
- Install the dependencies
`pip install -r requirements.txt`
- Then run the program with `python main.py`
- Configuration can be done through
  - `config.ini` in the project directory
  - `~/.config/iptic-memex/config.ini` in the user directory
  - via custom .ini file with `-c` or `--conf` flag
- API key can be set in the config file as `api_key` or via environment variable `OPENAI_API_KEY`.
- Usage is well documented with click and can be accessed with `python main.py --help` or `<subcommand> --help`
- From within `chat` mode you can access the following commands: 
  - Access the help menu with `help` or `?`.
  - Load a conversation from a file with `load`.
    - Enter a filename to load a conversation from the chats directory.
    - From the 'load' subcommand you can then use `list` or `ls` to list the available conversations.
    - Tab completion is supported in Unix environments.
    - Exit out of the subcommand with `exit` or `quit`.
  - Save a conversation to a file with `save`.
    - Enter a filename to save the conversation to the chats directory.
    - From the 'save' subcommand you can then use `list` or `ls` to list the available conversations.
    - Tab completion is supported in Unix environments.
    - Exit out of the subcommand with `exit` or `quit`.
  - Clear the context and start a new conversation with `clear`.
  - Dump the chat message array for debugging with `show messages`.
  - Show token length of current session with `show tokens`.

### Chat about a file or URL

![Imgur Image](https://i.imgur.com/XGxn7my.gif)

One of the more useful ways to use this program is to chat or ask questions about a file or URL. This can be done by 
supplying one or more `--file` (`-f`) or `--url` (`-u`) or flags to the `chat` or `ask` subcommands. The file(s) will 
be loaded into the context through the prompt and available for you to ask questions about. URLs will be scraped for 
text and loaded into the context same as files. ID or Class can be used to select specific text scraped from a URL with
the `--class` or `--id` flags.

For example:
- `python main.py chat -f problem_code.py`
- `python main.py ask -f code.txt -f logfile.txt`
- `python main.py chat -u iptic.com`
- `python main.py chat -u iptic.com/about --class fl-node-606779cc7ec16`

**Note:** 
- Attaching large files can quickly exceed the token limit of your chosen model. Often it can be useful to copy the 
relevant parts into a new file and chat about that instead. For example, a particular function or method. 

- The token count of the attached context is displayed before the first request is sent to the API, giving you a chance to 
exit before incurring a cost. A notification will appear when the estimated remaining tokens is less than max_tokens. 


# Changelog

### 1.3.0 (05/29/2024)
- Added support for Anthropic and Google Gemini models 
- Removed OpenRouter support temporarily  
- Added models.ini for storing detailed model information
- Changed how model information is loaded and displayed 

### 1.2.4 (04/15/2023)
- Added ability to track token usage in chat mode with tiktoken
  - New `context_window` option in config.ini for use in calculating remaining tokens
  - Added `show tokens` command in chat mode to show current token count
  - When loading context into chat mode the token count will be displayed before first request is sent
  - A notice will display when *max_tokens* exceeds the estimated remaining tokens

### 1.2.3 (04/08/2023)
- Added ability to scrape text from a URL to be added into context for both chat and ask modes
- Scraped text can be filtered by ID or Class

### 1.1.0 (04/03/2023) 
- Added ability to accept multiple '-f' options for all modes

# License

This project is licensed under the terms of the MIT license. 

