# Installation Guide

## Quick Start
```bash
pip install -r requirements.txt
```
This installs all dependencies for all AI providers. For customized installations (skip providers you don't need), see [Custom Installation](#custom-installation).

## Requirements

- Python 3.8+
- pip

## Base Requirements (All Platforms)

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
PyPDF2
python-docx
openpyxl
pytz
```
## Platform-Specific Notes

### Windows
- `gnureadline` is not available and not needed on Windows
- `llama-cpp-python` may require additional setup. See [llama-cpp-python installation guide](https://github.com/abetlen/llama-cpp-python)

### macOS
- All dependencies should install normally

### Linux
- All dependencies should install normally
- GPU acceleration may require additional system packages

## Provider-Specific Dependencies

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
google-generativeai
```
### Cohere
```
cohere
```
### Local Models (Llama, etc.)
```
llama-cpp-python
```
## Custom Installation

### Example 1: Windows without local models

Create a custom `requirements.txt`:
```
openai
anthropic
google-generativeai
click
Pygments
requests
bs4
beautifulsoup4
tiktoken
reportlab
trafilatura
cohere
PyPDF2
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
PyPDF2
python-docx
openpyxl
pytz
gnureadline;platform_system=="Linux" or platform_system=="Darwin"
```
## Configuration

After installation:

1. Copy `config.ini.example` to `config.ini`
2. Edit `config.ini` to set your API keys and preferred models
3. Run `python memex.py --help` to see available options