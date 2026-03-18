# lc

A simple, interactive OpenAI-compatible chat client for the terminal.

## Features

- Interactive chat with any OpenAI-compatible API
- Streaming responses with markdown rendering
- Tool calling: math, file read/write/edit, shell commands, glob, grep, directory listing, mermaid diagrams
- User approval required for write and command operations
- Thinking/reasoning token display for supported models
- Interactive model selection
- Configurable system prompt
- Token tracking and throughput display

## Install

```
pip install lc-cli
```

## Usage

Set your API key:

```
export OPENAI_API_KEY=your-key-here
```

Run:

```
lc
```

Or point at a different OpenAI-compatible host:

```
lc --host https://your-api-host.com
```

### Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/tools` | List available tools |
| `/model` | Select model interactively |
| `/model <name>` | Set model directly |
| `/prompt` | Set or clear the system prompt |
| `/clear` | Clear conversation history |
| `/exit` | Exit |

### System Prompt

Place a default system prompt at `~/.config/lc/prompt.txt` and it will be loaded automatically.

Or specify one explicitly:

```
lc --system-prompt-file path/to/prompt.txt
```

## Requirements

- Python 3.8+
- openai >= 1.0.0
- prompt_toolkit >= 3.0.0
- rich >= 13.0.0

## License

MIT
