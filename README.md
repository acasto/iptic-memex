# Description

Iptic Memex is a Python program that offers a straight-forward CLI interface for interacting with LLM providers through their APIs. Currently tested with OpenAI, Anthropic, Google Gemini, OpenRouter, and llama.cpp server. Input can be piped in from the command line or entered interactively through 'ask' and 'chat' modes. Chat mode features the ability to save conversations in a human-readable conversation format with a configurable extension for use with external applications such as Obsidian.

The name is a reference to the Memex, a device described by Vannevar Bush in his 1945 essay "As We May Think" which he
envisioned a device that would compress and store all of their knowledge. https://en.wikipedia.org/wiki/Memex

![Imgur Image](https://i.imgur.com/XLJ4AuY.gif)

# Features

- **Multiple Interaction Modes**:
  - [x] CLI mode for quick questions or scripting
  - [x] Chat mode for extended conversations
  - [x] Ask mode for single questions
  - [x] Completion mode for processing files or stdin

- **Context Management**:
  - [x] Load files into context in chat and ask modes
  - [x] Fetch and include content from the web
  - [x] Search the web and load results into context
  - [x] Easily add multi-line content
  - [x] Select and import parts of Python files
  - [x] Add a project context for encapsulating other contexts

- **Provider Flexibility**:
  - [x] Supports multiple LLM providers. Currently:
      - OpenAI
      - Anthropic
      - Google Gemini
      - OpenRouter
      - Llama.cpp via API
  - [x] OpenAI compatibile providers can be added through configs, no code changes needed
  - [x] Easy configuration of providers and models through config files
  - [x] Switch between providers and models on the fly
  - [x] Providers can be aliased in the config file for per provider or model settings

- **Conversation Handling**:
  - [x] Save and load conversations in human-readable formats
  - [x] Export conversations to various formats (markdown, txt, pdf)
  - [x] Context management for optimizing token usage
  - [x] Token usage tracking and management where applicable
  - [x] Easily save code blocks from repsonses to files

- **Enhanced User Experience**:
  - [x] Streaming support for real-time responses
  - [x] Syntax highlighting for code blocks in chat
  - [x] Token usage tracking and context management
  - [x] Tab completion for file paths, commands, and settings

- **Extensibility**:
  - [x] Modular action system for easy feature additions
  - [x] Custom context handlers for various input types

# Installation & Usage

## Basic Usage

*The program is still in development and may have bugs or issues. Please report any problems you encounter.*

- Clone the repository
`git clone https://github.com/acasto/iptic-memex.git`
- Install the dependencies
`pip install -r requirements.txt`
- Then run the program with `python main.py`
- Configuration can be done through
  - `config.ini` in the project directory
  - `~/.config/iptic-memex/config.ini` in the user directory
  - via custom .ini file with `-c` or `--conf` flag
- Model configuration can be done through
  - `models.ini` in the project directory
  - `~/.config/iptic-memex/models.ini` in the user directory
- API key can be set in the config file as `api_key` or via environment variables. (e.g. `OPENAI_API_KEY`)
- Usage is well documented with click and can be accessed with `python main.py --help` or `<subcommand> --help`

## Key Commands

- `python main.py --help`: Display general help
- `python main.py <subcommand> --help`: Show help for a specific subcommand
- `python main.py chat`: Enter chat mode
- `python main.py ask`: Enter ask mode
- `python main.py chat -f <filename>`: Chat about a specific file

## Chat Mode Commands

While in chat mode, you can use the following commands:

- `help`: Display a list of available commands
- `quit` or `exit`: Exit the chat mode
- `load project`: Load a project into the context
- `load file`: Load a file into the context
- `load code`: Load code snippets into the context
- `load multiline`: Load multiple lines of text into the context
- `load web`: Load content from a web page into the context
- `load soup`: Fetch content from a web page using BeautifulSoup
- `load search`: Perform a web search and load results
- `clear context`: Clear a specific item from the context
- `clear chat`: Reset the entire conversation state
- `clear last [n]`: Remove the last n messages from the chat history
- `clear first [n]`: Remove the first n messages from the chat history
- `clear`: Clear the screen
- `reprint`: Reprint the entire conversation
- `show settings`: Display all current settings
- `show models`: List all available models
- `show messages`: Display all messages in the current chat
- `show usage`: Show token usage statistics
- `set option`: Modify a specific option or setting
- `save chat`: Save the current chat session
- `save last`: Save only the last message of the chat
- `save full`: Save the full conversation including context
- `save code`: Extract and save code blocks from the conversation
- `load chat`: Load a previously saved chat session
- `list chats`: Display a list of all saved chat sessions
- `export chat`: Export the current chat in a specified format

These commands provide extensive control over the chat environment, allowing you to manage context, manipulate the conversation history, adjust settings, and interact with external resources seamlessly.

## Configuration

- `config.ini`: Main configuration file
- `models.ini`: Detailed model information and settings
- User-specific configurations can be added in `~/.config/iptic-memex/config.ini` and `~/.config/iptic-memex/models.ini`

## Add an OpenAI compatible provider

To add a new OpenAI compatible provider, just add a section to config.ini in the following format along with any other settings you may want to override. 
```
[provider_name]
alias = OpenAI
base_url = <the provider's base URL>
```
Then you just need to add the models to models.ini like so:
```
[model short name]
provider = <the provider you setup>
model_name = <the full official model name>
context_size = 4096
response_label = "> My Model: "
```


### Chat about a file or URL

![Imgur Image](https://i.imgur.com/XGxn7my.gif)

One of the more useful ways to use this program is to chat or ask questions about a file or URL. This can be done by 
supplying one or more `--file` (`-f`) to the `chat` or `ask` subcommands. The file(s) will be loaded into the context through the prompt and available for you to ask questions about. Web context has been moved to the chat mode and can be accessed with the `load web`, `load soup`, and `load search` commands. 

For example:
- `python main.py chat -f problem_code.py`
- `python main.py ask -f code.txt -f logfile.txt`
- From within chat mode: `load file`, `load web`, or `load soup`

Note: `load web` uses the trafilatura library to retrieve a more simplified version of the web page, while `load soup` uses BeautifulSoup to scrape the raw HTML. Evenually these will probably be merged into a single more robust command. 

### Search the web

The `load search` action is currently based on the Breave Search API **summarization** endpoint and gets added to the context the same as others (e.g. chatting with a file). The API key is set the same as other providers. Support will eventually be added for other search providers and configurations. 

### Multiline Input
The `load multiline` command allows you to add multiple lines of text to the context. This can be useful for adding code snippets, error messages, or other multi-line content.

### Saving code blocks

Now that LLMs are getting better at producing functional code, the ability to save a code block instead of just copying it out is useful. The `save code` command will extract code blocks from the most recent assistant reponse and provide a file save dialog. (`save code <n>` can be used to parse the last-n responses). If multiple code blocks are present you will be presented with a choice of which to save.

![Imgur Image](https://i.imgur.com/U8Tzg4Y.png)

### Projects

The project context (`load project`) causes the other contexts (e.g. file, web, multiline, etc.) to be wrapped in a common project context tags with a project name and project notes. You can add these other contexts from the `load project` dialog.

# Changelog

### 2.0.0 (Current)
- Implemented a more extensible architecture with a revamped provider system
- Introduced modular actions and contexts for easier functionality extension
- Enhanced support for multiple LLM providers (OpenAI, Anthropic, Google)
- Improved configuration management with separate model configurations
- Refactored code structure for better maintainability and extensibility
- Removed the URL command line arguments in favor of more robust context management in chat mode

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

