# Claude Code Instructions for takopi-party

## Pre-commit Checks

Before committing and pushing, always run:

```bash
uv run ruff check . --fix
uv run ruff format .
```

## Project Structure

- `src/takopi_party/` - Main package source
  - `plugin.py` - Command backend implementation (handles /party commands)
  - `config.py` - TOML and topic state file management
  - `state.py` - PartyStateStore for persistence
  - `workspace.py` - Workspace management (git init, archive)

## Commands

The plugin provides 4 commands:

| Command | Handler | Description |
|---------|---------|-------------|
| `/party <name>` | `_handle_create` | Create project + topic + trigger |
| `/party leave` | `_handle_leave` | Unregister + archive |
| `/party list` | `_handle_list` | Show all topics |
| `/party help` | `_handle_help` | Show help |

## Development Commands

```bash
uv sync --dev          # Install dependencies
uv run ruff check .    # Lint
uv run ruff format .   # Format
uv run pytest          # Run tests
```

## Release Process

1. Update version in `pyproject.toml`
2. Commit changes
3. Create and push tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
4. Create GitHub release - PyPI publish happens automatically

## Key Dependencies

- Requires `takopi>=0.21.0` for `invoke_command` support (PR #151)
- Uses `invoke_command("topic", ...)` and `invoke_command("trigger", ...)` to orchestrate topic creation
