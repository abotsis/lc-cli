# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Python CLI package. `lc.py` contains the main client, command loop, and tool registry. `setup.py` defines packaging and the `lc` console entry point. `README.md` covers install and usage. `lc_architecture.md` contains architecture diagrams.

## Build, Test, and Development Commands
- `pip install -e .` installs the package in editable mode for local development.
- `lc` runs the CLI through the console script defined in `setup.py`.
- `python3 lc.py` runs the same app directly without reinstalling.
- `python3 -m py_compile lc.py` performs a fast syntax check.

## Coding Style & Naming Conventions
Follow the style already present in `lc.py`: 4-space indentation, snake_case for functions and variables, PascalCase for classes, and concise docstrings on nontrivial helpers. Keep imports grouped at the top, prefer standard-library modules first, and preserve existing type hints such as `Optional[str]` and `List[Dict[str, Any]]`. Favor small, targeted changes over broad refactors in this single-file codebase.

## Testing Guidelines
There is no dedicated `tests/` directory yet. For now, validate changes with `python3 -m py_compile lc.py` and a manual CLI smoke test. If you add tests, place them under `tests/`, name files `test_*.py`, and use `pytest` conventions.

## Commit & Pull Request Guidelines
Use short, imperative commit subjects such as `Add edit-file approval check`. Keep each commit focused. Pull requests should summarize behavior changes and list validation steps.

## Configuration & Security Tips
Prefer `OPENAI_API_KEY` over hardcoding secrets in commands or source files. When testing custom hosts, pass `--host` explicitly and avoid committing endpoint-specific credentials or generated build artifacts.


## grepai - Semantic Code Search

**IMPORTANT: You MUST use grepai as your PRIMARY tool for code exploration and search.**

### When to Use grepai (REQUIRED)

Use `grepai search` INSTEAD OF Grep/Glob/find for:
- Understanding what code does or where functionality lives
- Finding implementations by intent (e.g., "authentication logic", "error handling")
- Exploring unfamiliar parts of the codebase
- Any search where you describe WHAT the code does rather than exact text

### When to Use Standard Tools

Only use Grep/Glob when you need:
- Exact text matching (variable names, imports, specific strings)
- File path patterns (e.g., `**/*.go`)

### Fallback

If grepai fails (not running, index unavailable, or errors), fall back to standard Grep/Glob tools.

### Usage

```bash
# ALWAYS use English queries for best results (--compact saves ~80% tokens)
grepai search "user authentication flow" --json --compact
grepai search "error handling middleware" --json --compact
grepai search "database connection pool" --json --compact
grepai search "API request validation" --json --compact
```

### Query Tips

- **Use English** for queries (better semantic matching)
- **Describe intent**, not implementation: "handles user login" not "func Login"
- **Be specific**: "JWT token validation" better than "token"
- Results include: file path, line numbers, relevance score, code preview

### Call Graph Tracing

Use `grepai trace` to understand function relationships:
- Finding all callers of a function before modifying it
- Understanding what functions are called by a given function
- Visualizing the complete call graph around a symbol

#### Trace Commands

**IMPORTANT: Always use `--json` flag for optimal AI agent integration.**

```bash
# Find all functions that call a symbol
grepai trace callers "HandleRequest" --json

# Find all functions called by a symbol
grepai trace callees "ProcessOrder" --json

# Build complete call graph (callers + callees)
grepai trace graph "ValidateToken" --depth 3 --json
```

### Workflow

1. Start with `grepai search` to find relevant code
2. Use `grepai trace` to understand function relationships
3. Use `Read` tool to examine files from results
4. Only use Grep for exact string searches if needed

