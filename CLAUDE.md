# Claude Code Instructions for takopi-party

## Pre-commit Checks

Before committing and pushing, always run:

```bash
uv run ruff check . --fix
uv run ruff format .
```

## Project Structure

- `src/takopi_party/` - Main package source
  - `plugin.py` - Command backend implementation
  - `state.py` - PartyStateStore for persistence
  - `workspace.py` - Workspace management

## Development Commands

```bash
uv sync --dev          # Install dependencies
uv run ruff check .    # Lint
uv run ruff format .   # Format
uv run pytest          # Run tests (when added)
```

## Release Process

1. Update version in `pyproject.toml`
2. Commit changes
3. Create and push tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
4. Create GitHub release - PyPI publish happens automatically
