"""TOML config file management for party plugin.

This module provides functions to append/remove project entries from
the takopi.toml config file, enabling hot-reload integration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomli
import tomli_w


def _load_config(config_path: Path) -> dict[str, Any]:
    """Load TOML config file."""
    with config_path.open("rb") as f:
        return tomli.load(f)


def _save_config(config_path: Path, config: dict[str, Any]) -> None:
    """Save TOML config file."""
    with config_path.open("wb") as f:
        tomli_w.dump(config, f)


def _make_project_key(name: str) -> str:
    """Generate a unique project key for takopi config.

    Names are sanitized to be valid TOML keys (lowercase, hyphens).
    """
    # Sanitize name for TOML key
    sanitized = name.lower().replace(" ", "-").replace("_", "-")
    # Remove any non-alphanumeric chars except hyphens
    sanitized = "".join(c for c in sanitized if c.isalnum() or c == "-")
    # Remove consecutive hyphens
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    sanitized = sanitized.strip("-")

    return f"party-{sanitized}"


def add_party_project(
    config_path: Path,
    workspace_path: Path,
    name: str,
) -> str:
    """Add a party project entry to takopi.toml.

    Args:
        config_path: Path to takopi.toml
        workspace_path: Absolute path to the workspace directory
        name: Human-readable topic name

    Returns:
        The project key that was added (for use with /ctx set)

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If project key already exists
    """
    config = _load_config(config_path)

    # Ensure projects dict exists
    if "projects" not in config:
        config["projects"] = {}

    project_key = _make_project_key(name)

    # Check for collision
    if project_key in config["projects"]:
        raise ValueError(f"Project key '{project_key}' already exists in config")

    # Add project entry
    config["projects"][project_key] = {
        "path": str(workspace_path),
        "worktrees_dir": ".worktrees",
        "default_engine": "claude",
    }

    _save_config(config_path, config)
    return project_key


def remove_party_project(config_path: Path, name: str) -> bool:
    """Remove a party project entry from takopi.toml.

    Args:
        config_path: Path to takopi.toml
        name: Human-readable topic name

    Returns:
        True if the project was removed, False if not found
    """
    try:
        config = _load_config(config_path)
    except FileNotFoundError:
        return False

    if "projects" not in config:
        return False

    project_key = _make_project_key(name)

    if project_key not in config["projects"]:
        return False

    del config["projects"][project_key]
    _save_config(config_path, config)
    return True


def get_party_project_key(name: str) -> str:
    """Get the project key for a party topic.

    This is useful for generating the /ctx set command hint.
    """
    return _make_project_key(name)


# Topic state binding (writes directly to telegram_topics_state.json)

TOPIC_STATE_FILENAME = "telegram_topics_state.json"
TOPIC_STATE_VERSION = 1


def _load_topic_state(state_path: Path) -> dict[str, Any]:
    """Load topic state JSON file."""
    import json

    if not state_path.exists():
        return {"version": TOPIC_STATE_VERSION, "threads": {}}
    with state_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_topic_state(state_path: Path, state: dict[str, Any]) -> None:
    """Save topic state JSON file."""
    import json
    import os

    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, state_path)


def bind_topic_to_project(
    config_path: Path,
    chat_id: int,
    thread_id: int,
    project_key: str,
    topic_title: str | None = None,
) -> None:
    """Bind a forum topic to a project in takopi's topic state.

    This directly writes to telegram_topics_state.json, which takopi
    reads to determine the context for each forum topic.

    Args:
        config_path: Path to takopi.toml (state file is in same directory)
        chat_id: Telegram chat ID
        thread_id: Forum topic thread ID
        project_key: Project key to bind to
        topic_title: Optional topic title for display
    """
    state_path = config_path.with_name(TOPIC_STATE_FILENAME)
    state = _load_topic_state(state_path)

    # Ensure threads dict exists
    if "threads" not in state:
        state["threads"] = {}

    # Create thread key (format: "chat_id:thread_id")
    thread_key = f"{chat_id}:{thread_id}"

    # Add or update thread entry
    thread_entry = state["threads"].get(thread_key, {})
    thread_entry["context"] = {"project": project_key}
    if topic_title:
        thread_entry["topic_title"] = topic_title

    state["threads"][thread_key] = thread_entry
    _save_topic_state(state_path, state)


def find_thread_for_project(
    config_path: Path,
    chat_id: int,
    project_key: str,
) -> int | None:
    """Find the thread_id for a project in takopi's topic state.

    Searches the topic state file for a thread bound to the given project.

    Args:
        config_path: Path to takopi.toml (state file is in same directory)
        chat_id: Telegram chat ID to search within
        project_key: Project key to find

    Returns:
        The thread_id if found, None otherwise.
    """
    state_path = config_path.with_name(TOPIC_STATE_FILENAME)

    import json

    try:
        state = _load_topic_state(state_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    threads = state.get("threads", {})
    prefix = f"{chat_id}:"

    for thread_key, entry in threads.items():
        if not thread_key.startswith(prefix):
            continue
        context = entry.get("context", {})
        if context.get("project") == project_key:
            # Extract thread_id from key "chat_id:thread_id"
            try:
                return int(thread_key.split(":")[1])
            except (IndexError, ValueError):
                continue

    return None


def unbind_topic(config_path: Path, chat_id: int, thread_id: int) -> bool:
    """Remove topic binding from takopi's topic state.

    Args:
        config_path: Path to takopi.toml
        chat_id: Telegram chat ID
        thread_id: Forum topic thread ID

    Returns:
        True if binding was removed, False if not found
    """
    state_path = config_path.with_name(TOPIC_STATE_FILENAME)

    import json

    try:
        state = _load_topic_state(state_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    thread_key = f"{chat_id}:{thread_id}"

    if "threads" not in state or thread_key not in state["threads"]:
        return False

    del state["threads"][thread_key]
    _save_topic_state(state_path, state)
    return True


def set_topic_trigger_mode(
    config_path: Path,
    chat_id: int,
    thread_id: int,
    mode: str,
) -> bool:
    """Set the trigger mode for a topic in takopi's topic state.

    Args:
        config_path: Path to takopi.toml (state file is in same directory)
        chat_id: Telegram chat ID
        thread_id: Forum topic thread ID
        mode: Trigger mode ("all" or "mentions")

    Returns:
        True if trigger mode was set, False if topic not found
    """
    state_path = config_path.with_name(TOPIC_STATE_FILENAME)

    import json

    try:
        state = _load_topic_state(state_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    thread_key = f"{chat_id}:{thread_id}"

    if "threads" not in state:
        state["threads"] = {}

    if thread_key not in state["threads"]:
        return False

    state["threads"][thread_key]["trigger_mode"] = mode
    _save_topic_state(state_path, state)
    return True
