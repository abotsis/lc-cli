__version__ = "0.1.0"

import sys
import os
import argparse
import base64
import difflib
import fnmatch
import json
import re
import shutil
import tempfile
import time
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any, List, Generator

import openai
from prompt_toolkit import PromptSession, prompt
from prompt_toolkit.application import Application
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.live import Live
from rich.syntax import Syntax
from rich.console import Group
from rich import box as richbox


console = Console()

STYLE = Style.from_dict(
    {
        "prompt": "ansicyan bold",
        "text": "ansiblack",
        "bottom-toolbar": "bg:ansiblack ansiwhite",
        "bottom-toolbar.text": "bg:ansiblack ansiwhite",
    }
)


class LCClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            print("Warning: No API key provided. Set OPENAI_API_KEY or use --api-key. Using dummy key.", file=sys.stderr)
            self.api_key = "sk-123"

        if base_url and not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"

        self.client = (
            openai.OpenAI(api_key=self.api_key, base_url=base_url)
            if base_url
            else openai.OpenAI(api_key=self.api_key)
        )
        self.history = InMemoryHistory()
        self.model = "gpt-3.5-turbo"
        self.system_prompt: Optional[str] = None
        self.messages: List[Dict[str, Any]] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cwd = os.getcwd()

    def list_models(self) -> List[str]:
        """Fetch available models from the API."""
        try:
            models = self.client.models.list()
            return sorted([m.id for m in models.data])
        except Exception:
            return []

    def stream_chat(
        self, message: str, tools: Optional[List[Dict]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Append a user message and stream a response."""
        self.messages.append({"role": "user", "content": message})
        try:
            yield from self._stream(tools)
        except Exception:
            self.messages.pop()
            raise

    def _build_system_prompt(self) -> Optional[str]:
        """Build system prompt with dynamic environment info."""
        if not self.system_prompt:
            return None
        parts = []
        # Dynamic environment context
        env_lines = [
            f"Current date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Working directory: {self.cwd}",
            f"Platform: {sys.platform}",
            f"Model: {self.model}",
        ]
        # Git info
        try:
            git_root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5, cwd=self.cwd,
            )
            if git_root.returncode == 0:
                repo_root = git_root.stdout.strip()
                env_lines.append(f"Git repository: {repo_root}")
                branch = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, timeout=5, cwd=self.cwd,
                )
                if branch.returncode == 0:
                    env_lines.append(f"Git branch: {branch.stdout.strip()}")
                status = subprocess.run(
                    ["git", "status", "--short"],
                    capture_output=True, text=True, timeout=5, cwd=self.cwd,
                )
                if status.returncode == 0:
                    changed = len([l for l in status.stdout.strip().splitlines() if l])
                    if changed:
                        env_lines.append(f"Git status: {changed} changed file(s)")
                    else:
                        env_lines.append("Git status: clean")
        except Exception:
            pass
        parts.append("# Environment\n" + "\n".join(f"- {l}" for l in env_lines))
        parts.append(self.system_prompt)
        return "\n\n".join(parts)

    def _stream(
        self, tools: Optional[List[Dict]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream a completion from the current messages."""
        start_time = time.time()
        messages = self.messages
        system_prompt = self._build_system_prompt()
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"cache_prompt": True},
        )
        for chunk in stream:
            yield {
                "chunk": chunk,
                "elapsed": time.time() - start_time,
            }

    def get_current_time(self) -> str:
        """Get current time formatted."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _find_all_occurrences(text, substring):
    """Return list of (start, end) index tuples for all occurrences of substring in text."""
    results = []
    start = 0
    while True:
        idx = text.find(substring, start)
        if idx == -1:
            break
        results.append((idx, idx + len(substring)))
        start = idx + 1
    return results


def _find_whitespace_normalized(content, old_string):
    """Find old_string in content using whitespace-normalized matching within each line."""
    def normalize_line(line):
        return " ".join(line.split())

    content_lines = content.splitlines(True)
    old_lines = old_string.splitlines(True)
    norm_old = [normalize_line(l) for l in old_lines]
    window = len(old_lines)
    results = []
    for i in range(len(content_lines) - window + 1):
        norm_content = [normalize_line(l) for l in content_lines[i : i + window]]
        if norm_content == norm_old:
            start = sum(len(l) for l in content_lines[:i])
            end = start + sum(len(l) for l in content_lines[i : i + window])
            results.append((start, end))
    return results


def _find_fuzzy_match(content, old_string, threshold=0.9):
    """Find the best fuzzy match for old_string in content above threshold."""
    content_lines = content.splitlines(True)
    old_lines = old_string.splitlines(True)
    window = len(old_lines)
    best_ratio = 0.0
    best_span = None
    for i in range(len(content_lines) - window + 1):
        candidate = "".join(content_lines[i : i + window])
        ratio = difflib.SequenceMatcher(None, candidate, old_string).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            start = sum(len(l) for l in content_lines[:i])
            end = start + len(candidate)
            best_span = (start, end)
    if best_ratio >= threshold and best_span:
        return [best_span]
    return []


def _no_match_error(content, old_string, path):
    """Build an error message with the closest matching line as a hint."""
    old_first_line = old_string.splitlines()[0].strip()
    best_ratio = 0.0
    best_line = ""
    best_lineno = 0
    for i, line in enumerate(content.splitlines(), 1):
        ratio = difflib.SequenceMatcher(None, line.strip(), old_first_line).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_line = line.rstrip()
            best_lineno = i
    msg = f"Error: No match found in {path} for the provided old_string."
    if best_ratio > 0.4:
        msg += f"\n\nClosest match (line {best_lineno}, {best_ratio:.0%} similar):\n  {best_line}"
    msg += "\n\nHint: Use read_file to verify the exact current content before editing."
    return msg


class ToolRegistry:
    def __init__(self):
        self.tools = {
            "math": {
                "type": "function",
                "function": {
                    "name": "math",
                    "description": "Perform mathematical calculations",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {
                                "type": "string",
                                "description": "Mathematical expression to evaluate (e.g., '2 + 2', '10 * 5', '100 / 4')",
                            }
                        },
                        "required": ["expression"],
                    },
                },
            },
            "current_time": {
                "type": "function",
                "function": {
                    "name": "current_time",
                    "description": "Get the current date and time",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            "write_file": {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file in the current directory. Requires user approval.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "Name of the file to write (must be in current directory, no path traversal)",
                            },
                            "content": {
                                "type": "string",
                                "description": "Content to write to the file",
                            },
                        },
                        "required": ["filename", "content"],
                    },
                },
            },
            "edit_file": {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Make targeted edits to a file by replacing a specific string with new content. "
                    "You MUST read the file first to get the exact current content. "
                    "Provide old_string with 3-5 lines of surrounding context to ensure a unique match. "
                    "If old_string is empty, creates a new file with new_string as content. "
                    "If new_string is empty, deletes the matched section. Requires user approval.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the file to edit",
                            },
                            "old_string": {
                                "type": "string",
                                "description": "Exact text to find and replace (empty string to create a new file)",
                            },
                            "new_string": {
                                "type": "string",
                                "description": "Replacement text (empty string to delete the matched section)",
                            },
                        },
                        "required": ["path", "old_string", "new_string"],
                    },
                },
            },
            "run_command": {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Run a shell command. Requires user approval. Use with caution.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The shell command to execute",
                            },
                        },
                        "required": ["command"],
                    },
                },
            },
            "glob": {
                "type": "function",
                "function": {
                    "name": "glob",
                    "description": "Find files by name pattern. Returns matching file paths relative to the working directory. Use for locating files when you know part of the name or extension.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Glob pattern to match (e.g., '*.py', '**/*.test.js', 'src/**/*.ts')",
                            },
                            "path": {
                                "type": "string",
                                "description": "Directory to search in (default: current working directory)",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
            "grep": {
                "type": "function",
                "function": {
                    "name": "grep",
                    "description": "Search file contents by regex pattern. Returns matching lines with file paths and line numbers. Use for finding specific code, definitions, usages, or text across files.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Regex pattern to search for (e.g., 'def main', 'class\\s+\\w+', 'TODO')",
                            },
                            "path": {
                                "type": "string",
                                "description": "File or directory to search in (default: current working directory)",
                            },
                            "include": {
                                "type": "string",
                                "description": "Glob pattern to filter which files to search (e.g., '*.py', '*.{js,ts}')",
                            },
                            "context_before": {
                                "type": "integer",
                                "description": "Number of lines to show before each match (like grep -B)",
                            },
                            "context_after": {
                                "type": "integer",
                                "description": "Number of lines to show after each match (like grep -A)",
                            },
                            "context": {
                                "type": "integer",
                                "description": "Number of lines to show before and after each match (like grep -C). Overridden by context_before/context_after if also set.",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
            "read_file": {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file. Supports optional line range to read specific sections of large files.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the file to read",
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Line number to start reading from (1-based, default: 1)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of lines to read (default: 500)",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            "list_directory": {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List the contents of a directory, showing files and subdirectories.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Directory path to list (default: current working directory)",
                            },
                        },
                        "required": [],
                    },
                },
            },
            "render_mermaid": {
                "type": "function",
                "function": {
                    "name": "render_mermaid",
                    "description": "Convert Mermaid diagram syntax to ASCII art for terminal display. Use when user requests to visualize a diagram in the terminal.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "diagram": {
                                "type": "string",
                                "description": "Mermaid diagram syntax to render (e.g., 'graph TD\n    A --> B')",
                            },
                            "width": {
                                "type": "integer",
                                "description": "Maximum width in characters (default: 80)",
                            },
                        },
                        "required": ["diagram"],
                    },
                },
            },
            "grepai": {
                "type": "function",
                "function": {
                    "name": "grepai",
                    "description": "Semantic code search and call graph tracing using grepai. "
                    "Use this as the PRIMARY tool for code exploration — prefer over grep/glob for intent-based queries. "
                    "Supports: 'search' for semantic code search, 'trace_callers' to find all callers of a symbol, "
                    "'trace_callees' to find all functions called by a symbol, 'trace_graph' for a full call graph.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "enum": ["search", "trace_callers", "trace_callees", "trace_graph"],
                                "description": "The grepai command: 'search' for semantic search, 'trace_callers'/'trace_callees'/'trace_graph' for call graph analysis",
                            },
                            "query": {
                                "type": "string",
                                "description": "For search: a natural language description of what to find (e.g., 'user authentication flow'). For trace commands: the symbol name (e.g., 'HandleRequest')",
                            },
                            "depth": {
                                "type": "integer",
                                "description": "Depth for trace_graph command (default: 3). Ignored for other commands.",
                            },
                        },
                        "required": ["command", "query"],
                    },
                },
            },
        }
        self.requires_approval = {"write_file", "edit_file", "run_command"}

    def needs_approval(self, tool_name: str) -> bool:
        return tool_name in self.requires_approval

    def execute_tool(self, tool_name: str, arguments: Dict) -> str:
        """Execute a tool and return the result."""
        if tool_name == "math":
            try:
                allowed_chars = set("0123456789+-*/(). %")
                expr = arguments.get("expression", "")
                if not all(c in allowed_chars for c in expr):
                    return "Error: Invalid characters in expression"
                result = eval(expr)
                return f"Result: {result}"
            except Exception as e:
                return f"Error calculating: {str(e)}"
        elif tool_name == "current_time":
            return f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        elif tool_name == "write_file":
            return self._write_file(arguments)
        elif tool_name == "edit_file":
            return self._edit_file(arguments)
        elif tool_name == "run_command":
            return self._run_command(arguments)
        elif tool_name == "glob":
            return self._glob(arguments)
        elif tool_name == "grep":
            return self._grep(arguments)
        elif tool_name == "read_file":
            return self._read_file(arguments)
        elif tool_name == "list_directory":
            return self._list_directory(arguments)
        elif tool_name == "render_mermaid":
            return self._render_mermaid(arguments)
        elif tool_name == "grepai":
            return self._grepai(arguments)
        else:
            return f"Unknown tool: {tool_name}"

    def _write_file(self, arguments: Dict) -> str:
        """Write content to a file."""
        filename = arguments.get("filename", "")
        content = arguments.get("content", "")

        if not filename:
            return "Error: No filename provided"

        if "/" in filename or ".." in filename:
            return "Error: Invalid filename (path traversal not allowed)"

        if filename.startswith("~"):
            return "Error: Home directory expansion not allowed"

        try:
            filepath = os.path.join(os.getcwd(), filename)
            with open(filepath, "w") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} characters to {filename}"
        except Exception as e:
            return f"Error writing file: {str(e)}"

    def _edit_file(self, arguments: Dict) -> str:
        """Edit a file by replacing old_string with new_string."""
        path = arguments.get("path", "")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")

        if not path:
            return "Error: No path provided"

        path = os.path.abspath(path)

        # Create new file when old_string is empty
        if not old_string:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(new_string)
                return f"Created new file {path} ({len(new_string)} characters)"
            except Exception as e:
                return f"Error creating file: {e}"

        # Read existing file
        if not os.path.isfile(path):
            return f"Error: File not found: {path}"
        try:
            with open(path, "r") as f:
                content = f.read()
        except Exception as e:
            return f"Error reading file: {e}"

        # Progressive matching: exact → whitespace-normalized → fuzzy
        match_method = "exact"
        matches = _find_all_occurrences(content, old_string)

        if not matches:
            match_method = "whitespace-normalized"
            matches = _find_whitespace_normalized(content, old_string)

        if not matches:
            match_method = "fuzzy"
            matches = _find_fuzzy_match(content, old_string)

        if not matches:
            return _no_match_error(content, old_string, path)

        if len(matches) > 1:
            return (
                f"Error: Found {len(matches)} matches for old_string in {path}. "
                "Please provide more surrounding context to uniquely identify the section to edit."
            )

        start, end = matches[0]
        new_content = content[:start] + new_string + content[end:]

        try:
            with open(path, "w") as f:
                f.write(new_content)
        except Exception as e:
            return f"Error writing file: {e}"

        # Compute affected line range
        start_line = content[:start].count("\n") + 1
        end_line = start_line + new_string.count("\n")
        return f"Successfully edited {path} (lines {start_line}-{end_line}, matched via {match_method})"

    def _run_command(self, arguments: Dict) -> str:
        """Run a shell command."""
        command = arguments.get("command", "")
        if not command:
            return "Error: No command provided"

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=os.getcwd(),
            )
            output = []
            if result.stdout:
                output.append(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                output.append(f"STDERR:\n{result.stderr}")
            output.append(f"Exit code: {result.returncode}")
            return "\n".join(output) if output else "Command completed with no output"
        except subprocess.TimeoutExpired:
            return "Error: Command timed out after 60 seconds"
        except Exception as e:
            return f"Error running command: {str(e)}"

    def _glob(self, arguments: Dict) -> str:
        """Find files matching a glob pattern."""
        import pathlib
        pattern = arguments.get("pattern", "")
        if not pattern:
            return "Error: No pattern provided"
        base = arguments.get("path", os.getcwd())
        base = os.path.abspath(base)
        if not os.path.isdir(base):
            return f"Error: Not a directory: {base}"
        base_path = pathlib.Path(base)
        # Simple patterns (no path separators) search recursively;
        # patterns with paths use exact path-aware matching.
        if "/" in pattern or os.sep in pattern:
            iterator = base_path.glob(pattern)
        else:
            iterator = base_path.rglob(pattern)
        matches = []
        for p in iterator:
            if not p.is_file():
                continue
            rel = p.relative_to(base_path)
            if any(part.startswith(".") for part in rel.parts):
                continue
            matches.append(str(rel))
            if len(matches) >= 200:
                break
        if not matches:
            return f"No files matching '{pattern}'"
        result = "\n".join(sorted(matches))
        if len(matches) >= 200:
            result += "\n... (truncated at 200 results)"
        return result

    def _grep(self, arguments: Dict) -> str:
        """Search file contents by regex."""
        pattern = arguments.get("pattern", "")
        if not pattern:
            return "Error: No pattern provided"
        base = arguments.get("path", os.getcwd())
        base = os.path.abspath(base)
        include = arguments.get("include", "")
        ctx = arguments.get("context", 0)
        before = arguments.get("context_before", ctx)
        after = arguments.get("context_after", ctx)
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: Invalid regex: {e}"
        results = []
        max_matches = 100
        match_count = [0]
        if os.path.isfile(base):
            self._grep_file(base, regex, os.path.dirname(base), results, max_matches, match_count, before, after)
        elif os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in files:
                    if include and not fnmatch.fnmatch(name, include):
                        continue
                    full = os.path.join(root, name)
                    self._grep_file(full, regex, base, results, max_matches, match_count, before, after)
                    if match_count[0] >= max_matches:
                        break
                if match_count[0] >= max_matches:
                    break
        else:
            return f"Error: Path not found: {base}"
        if not results:
            return f"No matches for '{pattern}'"
        result = "\n".join(results)
        if match_count[0] >= max_matches:
            result += "\n... (truncated at 100 matches)"
        return result

    def _grep_file(self, filepath: str, regex, base: str, results: List[str], limit: int,
                   match_count: List[int], before: int = 0, after: int = 0):
        """Search a single file for regex matches."""
        try:
            with open(filepath, "r", errors="ignore") as f:
                rel = os.path.relpath(filepath, base)
                if before > 0 or after > 0:
                    lines = f.readlines()
                    last_printed = -1
                    for lineno_idx, line in enumerate(lines):
                        if match_count[0] >= limit:
                            return
                        if regex.search(line):
                            match_count[0] += 1
                            start = max(0, lineno_idx - before)
                            end = min(len(lines), lineno_idx + after + 1)
                            if last_printed >= 0 and start > last_printed + 1:
                                results.append("--")
                            for i in range(start, end):
                                if i <= last_printed:
                                    continue
                                ln = i + 1
                                marker = ":" if i == lineno_idx else "-"
                                results.append(f"{rel}{marker}{ln}{marker} {lines[i].rstrip()[:200]}")
                                last_printed = i
                else:
                    for lineno, line in enumerate(f, 1):
                        if match_count[0] >= limit:
                            return
                        if regex.search(line):
                            match_count[0] += 1
                            results.append(f"{rel}:{lineno}: {line.rstrip()[:200]}")
        except (OSError, UnicodeDecodeError):
            pass

    def _read_file(self, arguments: Dict) -> str:
        """Read a file with optional line range."""
        path = arguments.get("path", "")
        if not path:
            return "Error: No path provided"
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return f"Error: File not found: {path}"
        offset = max(arguments.get("offset", 1), 1)
        limit = arguments.get("limit", 500)
        try:
            lines = []
            with open(path, "r", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if i < offset:
                        continue
                    if i >= offset + limit:
                        break
                    lines.append(f"{i:>6}\t{line.rstrip()}")
            if not lines:
                return f"No content in range (offset={offset}, limit={limit})"
            return "\n".join(lines)
        except Exception as e:
            return f"Error reading file: {e}"

    def _list_directory(self, arguments: Dict) -> str:
        """List directory contents."""
        path = arguments.get("path", os.getcwd())
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return f"Error: Not a directory: {path}"
        try:
            entries = sorted(os.listdir(path))
            result = []
            for name in entries:
                full = os.path.join(path, name)
                if os.path.isdir(full):
                    result.append(f"  {name}/")
                else:
                    size = os.path.getsize(full)
                    result.append(f"  {name} ({size} bytes)")
            return "\n".join(result) if result else "(empty directory)"
        except Exception as e:
            return f"Error listing directory: {e}"

    def _render_mermaid(self, arguments: Dict) -> str:
        """Render Mermaid diagram with mermaid-cli if available, else ASCII fallback."""
        diagram = arguments.get("diagram", "")
        width = arguments.get("width", 80)

        if not diagram:
            return "Error: No diagram provided"

        # Try mermaid-cli for high-quality rendering
        mmdc = shutil.which("mmdc")
        if mmdc:
            try:
                tmpdir = tempfile.mkdtemp(prefix="lc-mermaid-")
                input_path = os.path.join(tmpdir, "diagram.mmd")
                output_path = os.path.join(tmpdir, "diagram.png")
                with open(input_path, "w") as f:
                    f.write(diagram)
                proc = subprocess.run(
                    [mmdc, "-i", input_path, "-o", output_path,
                     "-b", "transparent", "-q", "-w", str(width * 10)],
                    capture_output=True, text=True, timeout=30,
                )
                if proc.returncode == 0 and os.path.isfile(output_path):
                    # Try inline display via iTerm2 protocol
                    displayed = self._display_image_inline(output_path)
                    if displayed:
                        return f"Rendered diagram (displayed inline). File: {output_path}"
                    return f"Rendered diagram to {output_path}\nOpen with: open {output_path}"
            except subprocess.TimeoutExpired:
                pass  # fall through to ASCII
            except Exception:
                pass  # fall through to ASCII

        # ASCII fallback
        return self._render_mermaid_ascii(diagram, width)

    def _display_image_inline(self, image_path: str) -> bool:
        """Try to display an image inline in the terminal. Returns True if successful."""
        # iTerm2 inline image protocol
        term = os.environ.get("TERM_PROGRAM", "")
        if term in ("iTerm.app", "WezTerm"):
            try:
                with open(image_path, "rb") as f:
                    image_data = base64.b64encode(f.read()).decode()
                size = os.path.getsize(image_path)
                name = base64.b64encode(os.path.basename(image_path).encode()).decode()
                sys.stdout.write(
                    f"\033]1337;File=name={name};size={size};inline=1:{image_data}\a\n"
                )
                sys.stdout.flush()
                return True
            except Exception:
                pass
        # Try imgcat command
        imgcat = shutil.which("imgcat")
        if imgcat:
            try:
                proc = subprocess.run([imgcat, image_path], timeout=5)
                return proc.returncode == 0
            except Exception:
                pass
        return False

    def _render_mermaid_ascii(self, diagram: str, width: int = 80) -> str:
        """Fallback ASCII rendering of a Mermaid diagram."""
        try:
            lines = diagram.strip().split(chr(10))
            result = []
            result.append("Mermaid Diagram (ASCII Preview):")
            result.append("=" * min(60, width))

            # Parse graph type
            graph_type = "graph"
            direction = "TB"
            for line in lines:
                line = line.strip()
                if line.startswith("graph") or line.startswith("flowchart"):
                    graph_type = line.split()[0]
                    if "TD" in line:
                        direction = "TD (top-down)"
                    elif "LR" in line:
                        direction = "LR (left-right)"
                    elif "RL" in line:
                        direction = "RL (right-left)"
                    elif "BT" in line:
                        direction = "BT (bottom-top)"
                    break

            result.append(f"Type: {graph_type}, Direction: {direction}")
            result.append("")

            # Extract nodes and edges
            nodes = set()
            edges = []

            for line in lines:
                line = line.strip()
                if not line or line.startswith('graph') or line.startswith('flowchart'):
                    continue

                edge_match = re.match(r'([A-Z]\w*(?:\[[^\]]*\])?(?:\{[^}]*\})?)\s*(-->|---|->|-#|<--|<<-)?\s*\|[^|]*\|\s*([A-Z]\w*(?:\[[^\]]*\])?(?:\{[^}]*\})?)', line)
                if not edge_match:
                    edge_match = re.match(r'([A-Z]\w*(?:\[[^\]]*\])?(?:\{[^}]*\})?)\s*(-->|---|->|-#|<--|<<-)?\s*([A-Z]\w*(?:\[[^\]]*\])?(?:\{[^}]*\})?)', line)
                if edge_match:
                    source = edge_match.group(1)
                    target = edge_match.group(3)
                    connector = edge_match.group(2) or "->"
                    nodes.add(source)
                    nodes.add(target)
                    edges.append((source, target, connector))

                subgraph_match = re.match(r'subgraph\s+(\w+)', line)
                if subgraph_match:
                    nodes.add(subgraph_match.group(1))

            result.append("Nodes:")
            for node in sorted(nodes):
                result.append(f"  \u2022 {node}")

            result.append("")
            result.append("Edges:")
            for source, target, connector in edges:
                arrow = "\u2192" if "--" in connector else "\u2194" if "<" in connector else "\u2192"
                result.append(f"  {source} {arrow} {target}")

            result.append("")
            result.append("-" * min(60, width))
            result.append("Tip: Install mermaid-cli for rendered diagrams:")
            result.append("  npm install -g @mermaid-js/mermaid-cli")

            return chr(10).join(result)

        except Exception as e:
            return f"Error rendering mermaid: {str(e)}"

    def _grepai(self, arguments: Dict) -> str:
        """Run grepai for semantic code search or call graph tracing."""
        command = arguments.get("command", "")
        query = arguments.get("query", "")
        if not command:
            return "Error: No command provided"
        if not query:
            return "Error: No query provided"

        grepai = shutil.which("grepai")
        if not grepai:
            return "Error: grepai is not installed or not on PATH. Install it and try again."

        try:
            if command == "search":
                cmd = [grepai, "search", query, "--json", "--compact"]
            elif command == "trace_callers":
                cmd = [grepai, "trace", "callers", query, "--json"]
            elif command == "trace_callees":
                cmd = [grepai, "trace", "callees", query, "--json"]
            elif command == "trace_graph":
                depth = str(arguments.get("depth", 3))
                cmd = [grepai, "trace", "graph", query, "--depth", depth, "--json"]
            else:
                return f"Error: Unknown grepai command: {command}"

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=os.getcwd(),
            )
            output = []
            if result.stdout:
                output.append(result.stdout)
            if result.stderr:
                output.append(f"STDERR:\n{result.stderr}")
            if result.returncode != 0:
                output.append(f"Exit code: {result.returncode}")
            return "\n".join(output) if output else "No results found"
        except subprocess.TimeoutExpired:
            return "Error: grepai command timed out after 60 seconds"
        except Exception as e:
            return f"Error running grepai: {str(e)}"

    def get_tools_list(self) -> Table:
        """Get a list of available tools."""
        table = Table(
            title="Available Tools", show_header=True, header_style="bold magenta"
        )
        table.add_column("Tool Name", style="cyan")
        table.add_column("Requires Approval", style="yellow")
        table.add_column("Description", style="white")

        for tool_name, tool in self.tools.items():
            approval = (
                "[red]Yes[/red]"
                if tool_name in self.requires_approval
                else "[green]No[/green]"
            )
            table.add_row(tool_name, approval, tool["function"]["description"])

        return table


def print_help():
    """Print help information."""
    help_text = """
[bold cyan]lc - Simple OpenAI Compatible Client[/bold cyan]

[bold]Commands:[/bold]
  [yellow]/help[/yellow]             Show this help message
  [yellow]/tools[/yellow]            List available tools
  [yellow]/model[/yellow]            Show current model or select interactively
  [yellow]/model <name>[/yellow]     Set model directly (e.g., /model gpt-4o)
  [yellow]/prompt[/yellow]           Set or clear the system prompt
  [yellow]/clear[/yellow]            Clear the conversation history
  [yellow]/exit[/yellow]             Exit the chat

[bold]Features:[/bold]
  • Interactive chat with OpenAI-compatible API
  • Streaming responses with markdown rendering
  • Tool calling support (math, current_time, write_file, edit_file, run_command)
  • User approval required for file writes and command execution
  • Token tracking and tokens/sec display
  • Command history

[bold]Environment Variables:[/bold]
  [yellow]OPENAI_API_KEY[/yellow]  Your OpenAI API key (or use --api-key)

[bold]Example Usage:[/bold]
  lc
  lc --host https://api.openai.com/v1
  lc --host https://your-openai-compatible-api.com

[bold]Quick Test:[/bold]
  [cyan]?[/cyan]  What is 2 + 2?
  [cyan]?[/cyan]  What time is it?
  [cyan]?[/cyan]  Write "Hello World" to test.txt
  [cyan]?[/cyan]  Run ls -la
    """
    console.print(Panel(help_text, title="[bold green]lc Help[/bold green]"))


# Maps tool_name -> set of approved directory prefixes
_approved_paths: Dict[str, set] = {}


def _get_tool_path(tool_name: str, arguments: Dict) -> Optional[str]:
    """Extract the absolute file path from a tool call's arguments."""
    if tool_name == "edit_file":
        p = arguments.get("path", "")
    elif tool_name == "write_file":
        p = arguments.get("filename", "")
        if not p or "/" in p or ".." in p:
            return None
        p = os.path.join(os.getcwd(), p)
    else:
        return None
    return os.path.abspath(p) if p else None


def _is_path_approved(tool_name: str, path: str) -> bool:
    """Check if a path falls under an already-approved directory."""
    for prefix in _approved_paths.get(tool_name, set()):
        if path == prefix or path.startswith(prefix + os.sep):
            return True
    return False


def get_user_approval(tool_name: str, arguments: Dict) -> bool:
    """Ask user for approval to execute a tool."""
    # Check path-based auto-approval
    tool_path = _get_tool_path(tool_name, arguments)
    if tool_path and _is_path_approved(tool_name, tool_path):
        console.print(f"[dim](auto-approved: {tool_name} in approved directory)[/dim]")
        return True

    console.print()
    if tool_name == "write_file":
        filename = arguments.get("filename", "unknown")
        content = arguments.get("content", "")
        preview = content[:200] + "..." if len(content) > 200 else content
        lexer = Syntax.guess_lexer(filename, code=preview)
        console.print(
            Panel(
                Group(
                    Text.from_markup(f"[bold]Filename:[/bold] {filename}"),
                    Syntax(preview, lexer),
                ),
                title="[bold yellow]write_file[/bold yellow]",
                title_align="left",
                border_style="yellow",
                box=richbox.HORIZONTALS,
            )
        )
    elif tool_name == "edit_file":
        path = arguments.get("path", "unknown")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")
        if not old_string:
            # Create new file
            preview = new_string[:500] + ("..." if len(new_string) > 500 else "")
            lexer = Syntax.guess_lexer(path, code=preview)
            console.print(
                Panel(
                    Group(
                        Text.from_markup(f"[bold]Create file:[/bold] {path}"),
                        Syntax(preview, lexer),
                    ),
                    title="[bold yellow]edit_file[/bold yellow]",
                    title_align="left",
                    border_style="yellow",
                    box=richbox.HORIZONTALS,
                )
            )
        else:
            # Show unified diff
            try:
                abs_path = os.path.abspath(path)
                with open(abs_path, "r") as f:
                    original = f.read()
                modified = original.replace(old_string, new_string, 1)
                diff_lines = list(
                    difflib.unified_diff(
                        original.splitlines(keepends=True),
                        modified.splitlines(keepends=True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                    )
                )
                if diff_lines:
                    diff_text = "".join(diff_lines)
                else:
                    diff_text = "(no differences — old_string may not match exactly; fuzzy matching will be attempted)"
            except FileNotFoundError:
                diff_text = f"(file not found: {path})"
            except Exception as e:
                diff_text = f"(could not generate diff: {e})"
            if diff_text.startswith("("):
                renderable = diff_text
            else:
                renderable = Syntax(diff_text, "diff")
            console.print(
                Panel(
                    renderable,
                    title="[bold yellow]edit_file[/bold yellow]",
                    title_align="left",
                    border_style="yellow",
                    box=richbox.HORIZONTALS,
                )
            )
    elif tool_name == "run_command":
        command = arguments.get("command", "unknown")
        console.print(
            Panel(
                f"[bold]Command:[/bold] {command}",
                title="[bold yellow]run_command[/bold yellow]",
                title_align="left",
                border_style="yellow",
                box=richbox.HORIZONTALS,
            )
        )
    else:
        console.print(
            f"[bold yellow]Tool '{tool_name}' requires approval.[/bold yellow]"
        )

    try:
        response = (
            prompt(
                FormattedText([("class:prompt", "Allow? [y/N/always]: ")]),
                style=STYLE,
            )
            .strip()
            .lower()
        )
        if response in ("always",):
            # Approve and record cwd as approved prefix for this tool
            cwd = os.getcwd()
            _approved_paths.setdefault(tool_name, set()).add(cwd)
            console.print(f"[dim](future {tool_name} in {cwd} auto-approved)[/dim]")
            return True
        return response in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        return False


def display_tool_call(tool_name: str, arguments: Dict):
    """Display tool name and parameters."""
    params = "  ".join(
        f"[bold]{k}[/bold]={rich_escape(repr(v) if not isinstance(v, str) else (v[:80] + '...' if len(v) > 80 else v))}"
        for k, v in arguments.items()
    )
    console.print(f"[bold cyan]{tool_name}[/bold cyan]  {params}")


def display_tool_result(tool_name: str, arguments: Dict, result: str):
    """Display tool result with syntax highlighting where appropriate."""
    if tool_name == "read_file" and not result.startswith("Error:") and not result.startswith("No content"):
        path = arguments.get("path", "")
        offset = max(arguments.get("offset", 1), 1)
        # Strip embedded line numbers to get raw source
        lines = []
        for line in result.split("\n"):
            parts = line.split("\t", 1)
            lines.append(parts[1] if len(parts) == 2 else line)
        code = "\n".join(lines)
        lexer = Syntax.guess_lexer(path, code=code)
        syntax = Syntax(code, lexer, line_numbers=True, start_line=offset)
        console.print(Panel(syntax, title=f"[bold]{tool_name}[/bold]", title_align="left", box=richbox.HORIZONTALS))
    else:
        console.print(Panel(result, title=f"[bold]{tool_name}[/bold]", title_align="left", box=richbox.HORIZONTALS))


def interactive_model_select(models: List[str], title: str = "Select a model") -> Optional[str]:
    """Interactive model selector with arrow key navigation."""
    if not models:
        console.print("[bold red]No models available.[/bold red]")
        return None

    selected_idx = [0]

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def move_up(event):
        selected_idx[0] = (selected_idx[0] - 1) % len(models)
        event.app.invalidate()

    @kb.add("down")
    @kb.add("j")
    def move_down(event):
        selected_idx[0] = (selected_idx[0] + 1) % len(models)
        event.app.invalidate()

    @kb.add("enter")
    def accept(event):
        event.app.exit(result=models[selected_idx[0]])

    @kb.add("c-c")
    @kb.add("escape")
    def cancel(event):
        event.app.exit(result=None)

    def get_text():
        lines = [("class:title", f" {title}\n\n")]
        for i, model in enumerate(models):
            if i == selected_idx[0]:
                lines.append(("class:selected", f"  ▸ {model}\n"))
            else:
                lines.append(("class:unselected", f"    {model}\n"))
        lines.append(("class:hint", "\n ↑/↓ navigate  Enter select  Esc cancel"))
        return lines

    select_style = Style.from_dict(
        {
            "title": "bold cyan",
            "selected": "bold bg:ansiblue fg:ansiwhite",
            "unselected": "",
            "hint": "dim italic",
        }
    )

    app = Application(
        layout=Layout(
            Window(FormattedTextControl(get_text), always_hide_cursor=True)
        ),
        key_bindings=kb,
        style=select_style,
        full_screen=False,
    )
    return app.run()


def select_model(client: LCClient, model_name: Optional[str] = None) -> Optional[str]:
    """Let user select a model from available models or set directly by name."""
    # If model name provided, set it directly without fetching list
    if model_name:
        client.model = model_name
        return model_name
    
    console.print("[bold cyan]Fetching available models...[/bold cyan]")
    models = client.list_models()

    if not models:
        console.print(
            "[bold yellow]Could not fetch models from API.[/bold yellow]"
        )
        console.print(
            "[dim]Type [cyan]/model <name>[/cyan] to set a model directly (e.g., [cyan]/model gpt-4o[/cyan])[/dim]"
        )
        return None

    return interactive_model_select(models)


DEFAULT_SYSTEM_PROMPT_PATH = os.path.expanduser("~/.config/lc/prompt.txt")


def load_default_system_prompt() -> Optional[str]:
    """Load default system prompt from standard paths."""
    try:
        with open(DEFAULT_SYSTEM_PROMPT_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def main():
    parser = argparse.ArgumentParser(description="lc - Simple OpenAI Compatible Client")
    parser.add_argument(
        "--api-key", help="OpenAI API key (or set OPENAI_API_KEY env var)"
    )
    parser.add_argument("--host", help="OpenAI API compatible host URL")
    parser.add_argument(
        "--system-prompt-file", help="Path to a system prompt file"
    )
    parser.add_argument("--version", action="version", version=f"lc {__version__}")

    args = parser.parse_args()

    client = LCClient(api_key=args.api_key, base_url=args.host)

    # Load system prompt: explicit flag > ~/.config/lc/prompt.txt > /tmp/prompt.txt
    if args.system_prompt_file:
        try:
            with open(args.system_prompt_file) as f:
                client.system_prompt = f.read().strip()
        except FileNotFoundError:
            console.print(f"[bold red]System prompt file not found: {args.system_prompt_file}[/bold red]")
            sys.exit(1)
    else:
        client.system_prompt = load_default_system_prompt()

    if args.host:
        console.print("[bold cyan]Fetching available models...[/bold cyan]")
        models = client.list_models()
        if models:
            selected = interactive_model_select(models, title="Select a model to use")
            if selected:
                client.model = selected
                console.print(f"[bold green]Using model: {selected}[/bold green]")
            else:
                console.print(f"[dim]Using default model: {client.model}[/dim]")
        else:
            console.print("[bold yellow]Could not fetch models, using default.[/bold yellow]")

    session = PromptSession(history=client.history)
    tool_registry = ToolRegistry()

    commands = ["/help", "/tools", "/model", "/prompt", "/clear", "/exit"]

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            # Only complete when the entire input so far is a slash command prefix
            if not text.startswith("/"):
                return
            for cmd in commands:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))

    completer = SlashCompleter()

    console.print(
        Panel(
            f"[bold green]lc - Simple OpenAI Compatible Client[/bold green]\n"
            f"Model: [cyan]{client.model}[/cyan]\n"
            "Type [cyan]/help[/cyan] for commands, or start chatting directly.",
            title="[bold]Welcome to lc[/bold]",
        )
    )

    def _format_size(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

    def _context_size():
        total = 0
        for m in client.messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content)
            tool_calls = m.get("tool_calls", [])
            for tc in tool_calls:
                total += len(tc.get("function", {}).get("arguments", ""))
        return total

    def status_line():
        tokens_up = f"\u2191 {client.prompt_tokens}"
        tokens_down = f"\u2193 {client.completion_tokens}"
        ctx = f"{len(client.messages)} msgs"
        ctx_size = f"ctx {_format_size(_context_size())}"
        return f" {client.model}  |  {ctx}  |  {ctx_size}  |  {tokens_up}  {tokens_down} "

    def bottom_toolbar():
        return [("class:bottom-toolbar", status_line())]

    def print_status():
        console.print(f"[dim on black]{status_line()}[/dim on black]")

    while True:
        try:
            prompt_text = FormattedText(
                [
                    ("class:prompt", "lc> "),
                ]
            )

            user_input = prompt(
                prompt_text,
                style=STYLE,
                completer=completer,
                auto_suggest=AutoSuggestFromHistory(),
                multiline=False,
                bottom_toolbar=bottom_toolbar,
            )

            if not user_input.strip():
                continue

            if user_input.startswith("/"):
                command = user_input[1:].strip().lower()
                if command == "help":
                    print_help()
                elif command == "tools":
                    console.print(tool_registry.get_tools_list())
                elif command == "model":
                    # Parse /model command with optional model name argument
                    parts = user_input[1:].strip().split(None, 1)
                    cmd_name = parts[0].lower()
                    model_arg = parts[1] if len(parts) > 1 else None
                    
                    if model_arg:
                        # Direct model setting: /model <name>
                        new_model = select_model(client, model_arg)
                        if new_model:
                            console.print(
                                f"[bold green]Model changed to: {new_model}[/bold green]"
                            )
                    else:
                        # Interactive selection or show current model
                        current_model = client.model
                        new_model = select_model(client)
                        if new_model:
                            console.print(
                                f"[bold green]Model changed to: {new_model}[/bold green]"
                            )
                        else:
                            # List endpoint failed, show current model
                            console.print(
                                f"[dim]Current model: [cyan]{current_model}[/cyan][/dim]"
                            )
                elif command == "prompt":
                    console.print(
                        "[bold cyan]Enter system prompt (submit with Esc+Enter, cancel with Ctrl-C):[/bold cyan]"
                    )
                    if client.system_prompt:
                        console.print(f"[dim]Current: {client.system_prompt}[/dim]")
                    try:
                        new_prompt = prompt(
                            FormattedText([("class:prompt", "prompt> ")]),
                            style=STYLE,
                            multiline=True,
                        ).strip()
                        if new_prompt:
                            client.system_prompt = new_prompt
                            console.print("[bold green]System prompt set.[/bold green]")
                        else:
                            client.system_prompt = None
                            console.print("[bold yellow]System prompt cleared.[/bold yellow]")
                    except (KeyboardInterrupt, EOFError):
                        console.print("[dim]Cancelled.[/dim]")
                elif command == "clear":
                    client.history = InMemoryHistory()
                    client.messages.clear()
                    console.print("[bold]Conversation history cleared.[/bold]")
                elif command == "exit":
                    console.print("[bold]Goodbye![/bold]")
                    sys.exit(0)
                else:
                    console.print(
                        f"[bold yellow]Unknown command: {command}[/bold yellow]"
                    )
                continue

            tools_list = list(tool_registry.tools.values())
            stream = client.stream_chat(user_input, tools=tools_list)

            while True:
                content_chunks = []
                thinking_chunks = []
                thinking_done = False
                thinking_start = None
                thinking_duration = 0.0
                thinking_tokens = 0
                tool_calls_data = {}
                total_tokens = 0
                duration = 0.0

                # Greyscale gradient for thinking box (dark at top → bright at bottom)
                _THINKING_GREYS = [239, 241, 243, 245, 247, 249, 251, 253]

                def render_thinking_box():
                    thinking_text = "".join(thinking_chunks)
                    if not thinking_text:
                        return None
                    lines = thinking_text.splitlines()
                    if not lines:
                        return None
                    visible = lines[-8:]
                    n = len(visible)
                    styled = Text()
                    for i, line in enumerate(visible):
                        grey_idx = (8 - n) + i
                        grey = _THINKING_GREYS[max(0, grey_idx)]
                        if i > 0:
                            styled.append("\n")
                        styled.append(line, style=f"color({grey})")
                    return Panel(
                        styled,
                        title="[dim]thinking[/dim]",
                        border_style="color(239)",
                        expand=True,
                    )

                def render_thinking_summary():
                    tok_str = f", {thinking_tokens} tokens" if thinking_tokens else ""
                    return Text(f"  thought for {thinking_duration:.1f}s{tok_str}", style="dim")

                def render_display():
                    parts = []
                    if thinking_done:
                        parts.append(render_thinking_summary())
                    elif thinking_chunks:
                        tp = render_thinking_box()
                        if tp:
                            parts.append(tp)
                    if content_chunks:
                        parts.append(Markdown("".join(content_chunks)))
                    if parts:
                        return Group(*parts)
                    return Text("")

                with Live(
                    render_display(),
                    console=console,
                    refresh_per_second=10,
                    vertical_overflow="visible",
                ) as live:
                    for data in stream:
                        chunk = data["chunk"]
                        duration = data["elapsed"]

                        if chunk.choices:
                            delta = chunk.choices[0].delta
                            reasoning = None
                            for _rkey in ("reasoning_content", "reasoning", "thinking"):
                                reasoning = getattr(delta, _rkey, None)
                                if reasoning:
                                    break
                            if not reasoning:
                                _extra = getattr(delta, "model_extra", None) or {}
                                for _rkey in ("reasoning_content", "reasoning", "thinking"):
                                    reasoning = _extra.get(_rkey)
                                    if reasoning:
                                        break
                            if reasoning:
                                if thinking_start is None:
                                    thinking_start = time.time()
                                thinking_chunks.append(reasoning)
                                live.update(render_display())

                        if chunk.choices and chunk.choices[0].delta.content:
                            if thinking_chunks and not thinking_done:
                                thinking_done = True
                                thinking_duration = time.time() - (thinking_start or time.time())
                            text = chunk.choices[0].delta.content
                            content_chunks.append(text)
                            live.update(render_display())

                        if chunk.choices and chunk.choices[0].delta.tool_calls:
                            if thinking_chunks and not thinking_done:
                                thinking_done = True
                                thinking_duration = time.time() - (thinking_start or time.time())
                            for tc in chunk.choices[0].delta.tool_calls:
                                idx = tc.index
                                if idx not in tool_calls_data:
                                    tool_calls_data[idx] = {
                                        "id": "",
                                        "name": "",
                                        "arguments": "",
                                    }
                                if tc.id:
                                    tool_calls_data[idx]["id"] = tc.id
                                if tc.function:
                                    if tc.function.name:
                                        tool_calls_data[idx]["name"] = tc.function.name
                                    if tc.function.arguments:
                                        tool_calls_data[idx]["arguments"] += (
                                            tc.function.arguments
                                        )

                        if hasattr(chunk, "usage") and chunk.usage:
                            total_tokens = chunk.usage.total_tokens
                            if hasattr(chunk.usage, "prompt_tokens") and chunk.usage.prompt_tokens:
                                client.prompt_tokens += chunk.usage.prompt_tokens
                            if hasattr(chunk.usage, "completion_tokens") and chunk.usage.completion_tokens:
                                client.completion_tokens += chunk.usage.completion_tokens
                            # Capture reasoning token count from usage details
                            details = getattr(chunk.usage, "completion_tokens_details", None)
                            if not details:
                                _uextra = getattr(chunk.usage, "model_extra", None) or {}
                                details = _uextra.get("completion_tokens_details")
                            if details:
                                if isinstance(details, dict):
                                    rt = details.get("reasoning_tokens")
                                else:
                                    rt = getattr(details, "reasoning_tokens", None)
                                if rt:
                                    thinking_tokens = rt

                # Finalize thinking if stream ended while still thinking
                if thinking_chunks and not thinking_done:
                    thinking_done = True
                    thinking_duration = time.time() - (thinking_start or time.time())

                # Print thinking summary line if there was thinking
                if thinking_chunks and thinking_done:
                    tok_str = f", {thinking_tokens} tokens" if thinking_tokens else ""
                    console.print(f"[dim]  thought for {thinking_duration:.1f}s{tok_str}[/dim]")

                console.print()
                print_status()

                # Build assistant message for history
                assistant_message: Dict[str, Any] = {"role": "assistant", "content": "".join(content_chunks) if content_chunks else ""}
                if tool_calls_data:
                    assistant_message["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in [
                            tool_calls_data[i] for i in sorted(tool_calls_data.keys())
                        ]
                    ]
                client.messages.append(assistant_message)

                # No tool calls — the model is done, break out
                if not tool_calls_data:
                    break

                # Execute tool calls and append results
                denied = False
                for idx in sorted(tool_calls_data.keys()):
                    tc = tool_calls_data[idx]
                    tool_name = tc["name"]
                    tool_id = tc["id"]
                    try:
                        arguments = json.loads(tc["arguments"])
                    except json.JSONDecodeError:
                        arguments = {}

                    if tool_registry.needs_approval(tool_name):
                        if get_user_approval(tool_name, arguments):
                            display_tool_call(tool_name, arguments)
                            result = tool_registry.execute_tool(tool_name, arguments)
                            display_tool_result(tool_name, arguments, result)
                            print_status()
                            client.messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_id,
                                    "content": result,
                                }
                            )
                        else:
                            console.print(f"[bold red]Denied: {tool_name}[/bold red]")
                            print_status()
                            denied = True
                            break
                    else:
                        display_tool_call(tool_name, arguments)
                        result = tool_registry.execute_tool(tool_name, arguments)
                        display_tool_result(tool_name, arguments, result)
                        print_status()
                        client.messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "content": result,
                            }
                        )

                if denied:
                    break

                # Send tool results back to the model for the next turn
                stream = client._stream(tools=tools_list)

            if total_tokens > 0 and duration > 0:
                tokens_per_sec = total_tokens / duration
                console.print(
                    f"[dim]{duration:.2f}s ({tokens_per_sec:.1f} tok/s)[/dim]"
                )
            elif duration > 0:
                console.print(f"[dim]{duration:.2f}s[/dim]")

        except KeyboardInterrupt:
            if client.messages and client.messages[-1].get("role") == "user":
                client.messages.pop()
            console.print(
                "\n[bold yellow]Interrupted. Use /exit to quit.[/bold yellow]"
            )
        except EOFError:
            console.print("\n[bold]Goodbye![/bold]")
            sys.exit(0)
        except Exception as e:
            if client.messages and client.messages[-1].get("role") == "user":
                client.messages.pop()
            console.print(f"[bold red]Error: {rich_escape(str(e))}[/bold red]")


if __name__ == "__main__":
    main()
