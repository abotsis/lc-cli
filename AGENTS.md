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
