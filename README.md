# Iptic Memex: Your Command Line Assistant

Iptic Memex is a Python program that offers a straight-forward CLI interface for interacting with large language models (LLMs). Input can be piped in from the command line or entered interactively in 'chat' mode. Chat mode features the ability to save conversations in a human-readable conversation format with a configurable extension for use with external applications such as Obsidian.

The name is a reference to the Memex, a device described by Vannevar Bush in his 1945 essay "As We May Think" which he
envisioned a device that would compress and store all of their knowledge. https://en.wikipedia.org/wiki/Memex

![Imgur Image](https://i.imgur.com/XLJ4AuY.gif)

---

## Features

- **Multiple Interaction Modes**
  - **Interactive 'chat' mode**: Engage in extended, multi-turn conversations with full context management and conversation history.
  - **Completion Mode**: For simple, one-shot queries, pipe your input directly into Memex. Useful for scripting and building LLM powered tools (e.g., summarize a file, describe an image)
  - **Completion Mode with Raw Response**: Get raw responses from an LLM provider, useful for debugging or when you need to access additional parts of the response object such as citations.
  
- **Advanced Context Management**
  - Load content from text files, PDFs, DOCX, XLSX, and even images.
  - For models without vision support you can load a summary of an image as describe by a model with vision support.
  - Fetch and integrate web content using simplified extraction (Trafilatura) or raw scraping (BeautifulSoup).
  - Add multiline text, code snippets, or specific segments from files into your conversation context.
  - Organize multiple content sources under a unified project view for focused sessions.

- **Broad LLM Provider Support**
  - Works seamlessly with providers such as OpenAI, Anthropic, Google Gemini, OpenRouter, Perplexity, Groq, Mistral, DeepSeek, Cohere, Fireworks AI, Together AI, and local Llama.cpp instances.
  - Easily add any OpenAI-compatible provider through configuration—no code changes required.
  - Define and switch between different models and settings on the fly.
  - Session usage stats and cost tracking with configurable session budget notification.

- **Smart Conversation Handling**
  - Save, load, and export entire chats in human-readable formats (Markdown, plain text, PDF).
  - Extract and save code blocks directly from responses, with the option to execute them (after confirmation).
  - Manage conversaton history with basic commands.

- **Powerful Integrated Assistant Tools**
  - **File System Access (%%FILE%%)**: Read, write, append, rename, delete, and even summarize file content directly from your conversation.
  - **Shell Commands (%%CMD%%)**: Execute commands straight from Memex. The %%CMD%% tool is fully configurable—it can run commands locally or use a Docker container based on your settings.
  - **Web Search (%%WEBSEARCH%%)**: Perform web searches via Perplexity Sonar and get cited, summarized results integrated into your context.
  - **Math Calculator (%%MATH%%)**: Tackle complex calculations using bc behind the scenes
  - **Memory (%%MEMORY%%)**: Store and recall facts or context across sessions in SQLite with support for project-specific memory.

- **Enhanced User Experience**
  - Real-time response streaming with syntax highlighting in code blocks.
  - Intelligent tab completion for file paths, commands, and settings.
  - Chained prompts and flexible templating support for sophisticated query design.
  - Prompt templating support from basic {{date}} to more advanced custom template actions.
  - A modular design that lets you easily extend or customize functionalities.
  - Support for user actions that can override or extend core actions, register user or assistant commands, and more.
  - An extensible SQLite persistence layer (currently used for stats and memories). 

---

## Installation & Usage

### Getting Started

See [INSTALL.md](INSTALL.md) for details on how to adjust requirements.txt as needed for your system.

#### 1. **Clone the Repository**
```bash
   git clone https://github.com/acasto/iptic-memex.git
   cd iptic-memex
```

#### 2. **Install Dependencies**
```bash
   pip install -r requirements.txt
```
#### 3. **Configuration**
- **Global Settings**:
  - `config.ini` in the project directory
  - `~/.config/iptic-memex/config.ini` for user-specific settings
  - Or specify a custom config file using the `-c` or `--conf` flag.
- **Models & Providers**:
  - `models.ini` in the project directory
  - `~/.config/iptic-memex/models.ini` for user-level configuration.
- **API Keys**:  
  Set keys in `config.ini` (e.g., `api_key`) or via environment variables like `OPENAI_API_KEY`.

#### 4. **Run Memex**
```bash
   python main.py chat
   ```

5. **Explore Help**

   For full usage details, run:

```bash
   python main.py --help
   python main.py <subcommand> --help
   <within chat mode> help
   ```
---

## Key Commands

- **General CLI Commands**
  - `python main.py --help`  
    Display overall help and list available subcommands.
  - `python main.py <subcommand> --help`  
    Get detailed help for specific commands (e.g., `chat`).


- **Interactive Mode**
  - `python main.py chat`  
    Launch interactive chat mode.


- **Completion Mode**:  
  Pipe a one-shot query directly into Memex. For example:

```bash
    echo "What is PI?" | memex -f -
 ```

- **Chat Mode Quick Reference**
  - **Context Loading**
    - `load file`, `load pdf`, `load doc`, `load sheet`, `load code`, `load multiline`, `load image` Import content from various file types or multiline text.
    - `load web`, `load soup`, `load search`  Add web content or perform a web search with summarized results.
    - `load raw` Load unformatted text into your conversation, useful for loading saved 'full' conversations to avoid double formatting.
    - `clear context` Clear the current turn context.
  - **Chat Management**
    - `save chat`, `save last`, `save full` Save the current conversation or specific parts of it.
    - `load chat` or `list chats` Manage saved chat sessions.
    - `export chat` Export the chat to a chosen format (Markdown, TXT, PDF).
    - `clear chat` Reset the current chat session.  
      Remove specific items or reset the complete conversation context.
    - `clear last`, `clear last n`, `clear first`, `clear first n`  
      Clear the last or first n messages from the chat history.
    - `reprint` Redisplay the entire chat history.
  - **Settings & Utility**
    - `show settings`, `show settings tools`, `show models`, `show messages`, `show usage`, `show cost` Inspect current configuration, active models, message history, token usage, and costs.
    - `set option`, `set option tools` Dynamically modify core settings and tool settings. 
  - **Integrated Tools**
    - `run code` Extract and execute code blocks (Python or Bash) from the assistant’s response (requires confirmation).
    - `save code` Save code blocks from the assistant’s response to a file.
    - `run command` Run an arbitrary shell command from the user side to include the output for the assistant.

---

## Configuration Details

### Adding OpenAI-Compatible Providers

To add a new OpenAI-compatible provider, add a section in your `config.ini` file:
```ini
[my_new_provider]
alias = OpenAI
base_url = <provider endpoing url>
api_key = <Your API Key or leave blank to use env variable>
extra_body = { ... }  ; Optional, for provider-specific parameters
```
Then, define your models in `models.ini`:
```ini
[my_model_short_name]
provider = my_new_provider
model_name = <official model name>
context_size = 8192
response_label = "> My Model: "
extra_body = { ... }  ; Optional, for model-specific settings
```


---

## Changelog

### 2.1.0 (02/06/2025) Current
- Major changes, not all listed here, REAMDE will be constantly updated
- Added vision/image support for OpenAI, Anthropic, and Google providers
- Added context caching where supported
- Added llama.cpp bindings provider for running models directly
- Added an SQLite persistence layer for stats and memories
- Added pseduo-tool implementation for assistant tool calling
  - Added math tool
  - Added file tool
  - Added a memory tool
  - Added a local and docker based command tool
  - Added websearch tool that utilizes Perplexity Sonar
- Moved core utiltiy functions to a new utils handler
- Added prompt chaining so you can have a prompt of prompts
- Added prompt templating with chaining for using multiple template handlers
- Added user action support for overriding core actions and ability to register usre or assistant commands
- Added an auto-submit capability for certain assistant commands to complete the next turn and get the results
- Added a spinner and new output handler
- Added a separate 'tools' argument for 'show settings, and 'set option' for tooling specific settings
- Added cost tracking and per session budget notification
- Added completion mode with raw response for debugging and raw response access
- Removed ask mode in favor of completion mode
- Removed various default models and updated OpenAI, Anthropic, and Goolge ones
- Various bug fixes

### 2.0.4 (09/03/2024)
- Added models/providers to models.ini
- Added trailing slash multiline to main chat
- Added wildcard support to 'load file'
- Added timeout setting for OpenAI provider
- Added a 'load raw' context for unwrapped context
- Added 'load sheet', 'load doc', and 'load pdf'
- Fixed minor bugs

### 2.0.3 (07/20/2024) Current
- Added the `run command` command to run shell commands and capture the output
- Added a provider class for Cohere
- Added entires in config.ini and models.ini to support Perplexity, Groq, Mistral, DeepSeek, and Cohere
- Adjusted the OpenAI provider so that stream usage can be disabled at the provider level (was breaking Mistral)

### 2.0.2 (07/16/2024)
- Added the `run code` command with the ability to capture the output for iterative troubleshooting

### 2.0.1 (07/15/2024)
- Minor bug fixes and improvements in error handling
- Updated README with new features and examples

### 2.0.0 (07/15/2024)
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