"""Tests for PartyCommand plugin."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from takopi_party.plugin import (
    PartyCommand,
    _get_thread_id,
)


@dataclass
class MockMessage:
    """Mock message for testing."""

    channel_id: int
    raw: dict[str, Any] | None = None
    thread_id: int | None = None
    sender_id: int | None = None


def make_raw_message(
    sender_id: int,
    thread_id: int | None = None,
) -> dict[str, Any]:
    """Create a mock raw Telegram message."""
    msg: dict[str, Any] = {
        "from": {
            "id": sender_id,
        }
    }
    if thread_id is not None:
        msg["message_thread_id"] = thread_id
    return msg


def make_context(
    args: list[str],
    chat_id: int,
    config_path: Path,
    raw_message: dict[str, Any] | None = None,
    workspace_base: str = "/party",
    sender_id: int | None = None,
    thread_id: int | None = None,
) -> MagicMock:
    """Create a mock CommandContext."""
    # Extract thread_id from raw_message if not provided explicitly
    if thread_id is None and raw_message:
        thread_id = raw_message.get("message_thread_id")
    # Extract sender_id from raw_message if not provided explicitly
    if sender_id is None and raw_message:
        from_user = raw_message.get("from")
        if isinstance(from_user, dict):
            sender_id = from_user.get("id")

    ctx = MagicMock()
    ctx.args = args
    ctx.message = MockMessage(
        channel_id=chat_id, raw=raw_message, thread_id=thread_id, sender_id=sender_id
    )
    ctx.config_path = config_path
    ctx.plugin_config = {
        "workspace_base": workspace_base,
    }
    # Make executor.send an async mock
    ctx.executor.send = AsyncMock()
    return ctx


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_thread_id_from_context(self) -> None:
        """Test extracting thread_id from context."""
        ctx = MagicMock()
        ctx.message = MockMessage(channel_id=123, thread_id=100)
        ctx.message.raw = None
        assert _get_thread_id(ctx) == 100

    def test_get_thread_id_from_raw(self) -> None:
        """Test extracting thread_id from raw message."""
        ctx = MagicMock()
        ctx.message = MockMessage(channel_id=123, thread_id=None)
        ctx.message.raw = {"message_thread_id": 200}
        assert _get_thread_id(ctx) == 200

    def test_get_thread_id_none(self) -> None:
        """Test extracting thread_id returns None when not in topic."""
        ctx = MagicMock()
        ctx.message = MockMessage(channel_id=123, thread_id=None)
        ctx.message.raw = None
        assert _get_thread_id(ctx) is None


@pytest.fixture
def tmp_config_path(tmp_path: Path) -> Path:
    """Return a temporary config path with initial config."""
    config_path = tmp_path / "takopi.toml"
    # Create a minimal valid config file
    config_path.write_text('default_engine = "claude"\nwatch_config = true\n')
    return config_path


@pytest.fixture
def workspace_base(tmp_path: Path) -> Path:
    """Return a temporary workspace base."""
    base = tmp_path / "party"
    base.mkdir()
    return base


@pytest.fixture
def party_command() -> PartyCommand:
    """Return a fresh PartyCommand instance."""
    return PartyCommand()


class TestPartyCommand:
    """Tests for PartyCommand handler."""

    async def test_handle_help(self, party_command: PartyCommand, tmp_config_path: Path) -> None:
        """Test /party help returns help text."""
        ctx = make_context(["help"], 99999, tmp_config_path)
        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "Party Mode Commands" in sent_msg.text
        assert "/party" in sent_msg.text

    async def test_handle_default_is_help(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party with no args shows help."""
        ctx = make_context([], 99999, tmp_config_path)
        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "Party Mode Commands" in sent_msg.text

    async def test_handle_list_empty(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party list with no topics."""
        ctx = make_context(["list"], 99999, tmp_config_path)
        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "No party topics" in sent_msg.text

    async def test_handle_leave_not_in_topic(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party leave outside of topic fails."""
        raw = make_raw_message(12345)  # No thread_id
        ctx = make_context(["leave"], 99999, tmp_config_path, raw_message=raw)
        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "inside a party topic" in sent_msg

    async def test_handle_leave_not_registered_topic(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
    ) -> None:
        """Test /party leave in unregistered topic fails."""
        raw = make_raw_message(12345, thread_id=100)
        ctx = make_context(
            ["leave"],
            99999,
            tmp_config_path,
            raw_message=raw,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "not a registered party topic" in sent_msg

    async def test_unknown_subcommand_triggers_create(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
    ) -> None:
        """Test that unknown subcommand (project name) triggers create flow."""
        raw = make_raw_message(12345)
        ctx = make_context(
            ["myproject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            workspace_base=str(workspace_base),
        )
        # Mock executor to avoid actual invoke_command
        ctx.executor.invoke_command = AsyncMock(side_effect=NotImplementedError)

        result = await party_command.handle(ctx)

        # Should fail because invoke_command not available, but should try
        assert result is None
        # Check last send call contains the error message
        last_call = ctx.executor.send.call_args_list[-1]
        sent_msg = last_call[0][0]
        assert "Command invocation not available" in sent_msg.text

    async def test_create_checks_duplicate_name(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
    ) -> None:
        """Test /party <name> fails if name already exists in state."""
        # First, manually register a topic in state
        store = party_command._get_store(
            make_context([], 99999, tmp_config_path, workspace_base=str(workspace_base))
        )
        await store.register_topic(
            chat_id=99999,
            thread_id=100,
            owner_id=12345,
            name="ExistingProject",
            workspace_path=str(workspace_base / "existing"),
        )

        # Now try to create with same name
        raw = make_raw_message(12345)
        ctx = make_context(
            ["ExistingProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "already exists" in sent_msg.text

    async def test_create_checks_workspace_collision(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
    ) -> None:
        """Test /party <name> fails if workspace already exists on disk."""
        # Create a workspace directory manually
        (workspace_base / "myproject").mkdir()

        raw = make_raw_message(12345)
        ctx = make_context(
            ["myproject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "already exists" in sent_msg.text

    async def test_create_requires_sender_id(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
    ) -> None:
        """Test /party <name> fails without sender_id."""
        ctx = make_context(
            ["myproject"],
            99999,
            tmp_config_path,
            raw_message=None,  # No raw message, no sender
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "Could not identify sender" in sent_msg


class TestLeaveCommand:
    """Tests for /party leave command."""

    async def test_leave_success(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
    ) -> None:
        """Test /party leave unregisters topic and archives workspace."""
        # Manually set up a registered topic
        store = party_command._get_store(
            make_context([], 99999, tmp_config_path, workspace_base=str(workspace_base))
        )

        # Create workspace
        project_path = workspace_base / "myproject"
        project_path.mkdir()
        (project_path / "README.md").write_text("test")

        await store.register_topic(
            chat_id=99999,
            thread_id=100,
            owner_id=12345,
            name="MyProject",
            workspace_path=str(project_path),
        )

        # Now leave
        raw = make_raw_message(12345, thread_id=100)
        ctx = make_context(
            ["leave"],
            99999,
            tmp_config_path,
            raw_message=raw,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "unregistered" in sent_msg.text
        assert "archived" in sent_msg.text.lower() or "cleaned up" in sent_msg.text.lower()

    async def test_leave_only_owner_can_leave(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
    ) -> None:
        """Test /party leave fails if not the owner."""
        # Manually set up a registered topic
        store = party_command._get_store(
            make_context([], 99999, tmp_config_path, workspace_base=str(workspace_base))
        )

        project_path = workspace_base / "myproject"
        project_path.mkdir()

        await store.register_topic(
            chat_id=99999,
            thread_id=100,
            owner_id=12345,  # Owner is 12345
            name="MyProject",
            workspace_path=str(project_path),
        )

        # Try to leave as different user
        raw = make_raw_message(99999, thread_id=100)  # Different sender
        ctx = make_context(
            ["leave"],
            99999,
            tmp_config_path,
            raw_message=raw,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "owner" in sent_msg.lower()


class TestListCommand:
    """Tests for /party list command."""

    async def test_list_shows_registered_topics(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
    ) -> None:
        """Test /party list shows registered topics."""
        # Manually set up some registered topics
        store = party_command._get_store(
            make_context([], 99999, tmp_config_path, workspace_base=str(workspace_base))
        )

        await store.register_topic(
            chat_id=99999,
            thread_id=100,
            owner_id=12345,
            name="ProjectA",
            workspace_path=str(workspace_base / "a"),
        )
        await store.register_topic(
            chat_id=99999,
            thread_id=200,
            owner_id=12345,
            name="ProjectB",
            workspace_path=str(workspace_base / "b"),
        )

        # List topics
        ctx = make_context(["list"], 99999, tmp_config_path)
        result = await party_command.handle(ctx)

        assert result is None
        ctx.executor.send.assert_called_once()
        sent_msg = ctx.executor.send.call_args[0][0]
        assert "ProjectA" in sent_msg.text
        assert "ProjectB" in sent_msg.text
