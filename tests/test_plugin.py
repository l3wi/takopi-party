"""Tests for PartyCommand plugin."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import tomli

from takopi_party.plugin import (
    PartyCommand,
    _extract_mentioned_user_id,
    _get_thread_id_from_message,
)


@dataclass
class MockMessage:
    """Mock message for testing."""

    channel_id: int
    raw: dict[str, Any] | None = None
    thread_id: int | None = None
    sender_id: int | None = None


@dataclass
class MockForumTopic:
    """Mock forum topic result."""

    message_thread_id: int


def make_raw_message(
    sender_id: int,
    thread_id: int | None = None,
    mentioned_user_id: int | None = None,
) -> dict[str, Any]:
    """Create a mock raw Telegram message."""
    msg: dict[str, Any] = {
        "from": {
            "id": sender_id,
        }
    }
    if thread_id is not None:
        msg["message_thread_id"] = thread_id
    if mentioned_user_id is not None:
        msg["entities"] = [
            {"type": "text_mention", "user": {"id": mentioned_user_id, "first_name": "User"}}
        ]
    return msg


def make_context(
    args: list[str],
    chat_id: int,
    config_path: Path,
    raw_message: dict[str, Any] | None = None,
    bot: Any = None,
    workspace_base: str = "/party",
    sender_id: int | None = None,
) -> MagicMock:
    """Create a mock CommandContext."""
    # Extract thread_id from raw_message if present
    thread_id = raw_message.get("message_thread_id") if raw_message else None
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
        "bot": bot,
        "workspace_base": workspace_base,
    }
    return ctx


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_thread_id_from_message(self) -> None:
        """Test extracting thread_id from message."""
        raw = make_raw_message(12345, thread_id=100)
        assert _get_thread_id_from_message(raw) == 100

        raw = make_raw_message(12345)
        assert _get_thread_id_from_message(raw) is None

        assert _get_thread_id_from_message(None) is None

    def test_extract_mentioned_user_id_valid(self) -> None:
        """Test extracting mentioned user ID from valid message."""
        raw = make_raw_message(12345, mentioned_user_id=67890)
        user_id = _extract_mentioned_user_id(raw)
        assert user_id == 67890

    def test_extract_mentioned_user_id_none(self) -> None:
        """Test extracting mentioned user ID from None returns None."""
        user_id = _extract_mentioned_user_id(None)
        assert user_id is None

    def test_extract_mentioned_user_id_no_entities(self) -> None:
        """Test extracting mentioned user ID when no entities."""
        raw = make_raw_message(12345)
        user_id = _extract_mentioned_user_id(raw)
        assert user_id is None

    def test_extract_mentioned_user_id_wrong_entity_type(self) -> None:
        """Test extracting mentioned user ID with wrong entity type."""
        raw: dict[str, Any] = {
            "from": {"id": 12345},
            "entities": [{"type": "bot_command", "offset": 0, "length": 5}],
        }
        user_id = _extract_mentioned_user_id(raw)
        assert user_id is None


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
def mock_bot() -> AsyncMock:
    """Return a mock bot client."""
    bot = AsyncMock()
    bot.create_forum_topic = AsyncMock(return_value=MockForumTopic(message_thread_id=100))
    return bot


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

        assert result is not None
        assert "Party Mode Commands" in result.text
        assert "/party register" in result.text

    async def test_handle_default_is_help(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party with no args shows help."""
        ctx = make_context([], 99999, tmp_config_path)
        result = await party_command.handle(ctx)

        assert result is not None
        assert "Party Mode Commands" in result.text

    async def test_handle_register_without_name_fails(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register without name fails."""
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is not None
        assert "Please provide a topic name" in result.text

    async def test_handle_register_project(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register ProjectName creates project topic."""
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is not None
        assert "MyProject" in result.text
        assert "created and ready to use" in result.text

    async def test_handle_register_project_duplicate_name_fails(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register with duplicate name fails."""
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        # First registration should succeed
        result = await party_command.handle(ctx)
        assert "MyProject" in result.text

        # Second registration with same name should fail
        mock_bot.create_forum_topic = AsyncMock(return_value=MockForumTopic(message_thread_id=200))
        result = await party_command.handle(ctx)
        assert "already exists" in result.text

    async def test_handle_register_no_sender(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register fails without sender info."""
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=None,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is not None
        assert "Could not identify sender" in result.text

    async def test_handle_list_empty(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party list with no topics."""
        ctx = make_context(["list"], 99999, tmp_config_path)
        result = await party_command.handle(ctx)

        assert result is not None
        assert "No party topics" in result.text

    async def test_handle_list_with_topics(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party list shows registered topics."""
        # Register a topic first
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "TestProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Now list
        ctx2 = make_context(["list"], 99999, tmp_config_path)
        result = await party_command.handle(ctx2)

        assert result is not None
        assert "TestProject" in result.text

    async def test_handle_topics_empty(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party topics with no topics for user."""
        raw = make_raw_message(12345)
        ctx = make_context(["topics"], 99999, tmp_config_path, raw_message=raw)
        result = await party_command.handle(ctx)

        assert result is not None
        assert "don't have any topics" in result.text

    async def test_handle_leave_not_in_topic(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party leave outside of topic fails."""
        raw = make_raw_message(12345)  # No thread_id
        ctx = make_context(["leave"], 99999, tmp_config_path, raw_message=raw)
        result = await party_command.handle(ctx)

        assert result is not None
        assert "inside a party topic" in result.text

    async def test_handle_leave_success(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party leave closes topic."""
        # Register a topic first
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Leave the topic (from within the topic)
        raw_in_topic = make_raw_message(12345, thread_id=100)
        ctx2 = make_context(
            ["leave"],
            99999,
            tmp_config_path,
            raw_message=raw_in_topic,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx2)

        assert result is not None
        assert "Goodbye" in result.text

    async def test_handle_allow_success(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party allow @user succeeds."""
        # Register a topic first
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Allow another user (from within the topic)
        raw_in_topic = make_raw_message(12345, thread_id=100, mentioned_user_id=67890)
        ctx2 = make_context(
            ["allow", "@someone"],
            99999,
            tmp_config_path,
            raw_message=raw_in_topic,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx2)

        assert result is not None
        assert "67890" in result.text
        assert "can now use" in result.text

    async def test_handle_allow_no_mention(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party allow without @mention fails."""
        # Register a topic first
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Try to allow without mention (from within the topic)
        raw_in_topic = make_raw_message(12345, thread_id=100)
        ctx2 = make_context(
            ["allow"],
            99999,
            tmp_config_path,
            raw_message=raw_in_topic,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx2)

        assert result is not None
        assert "@mention" in result.text

    async def test_handle_revoke_success(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party revoke @user succeeds."""
        # Register a topic first
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Allow another user
        raw_allow = make_raw_message(12345, thread_id=100, mentioned_user_id=67890)
        ctx_allow = make_context(
            ["allow", "@someone"],
            99999,
            tmp_config_path,
            raw_message=raw_allow,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx_allow)

        # Revoke the user
        raw_revoke = make_raw_message(12345, thread_id=100, mentioned_user_id=67890)
        ctx_revoke = make_context(
            ["revoke", "@someone"],
            99999,
            tmp_config_path,
            raw_message=raw_revoke,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx_revoke)

        assert result is not None
        assert "67890" in result.text
        assert "revoked" in result.text


class TestPartyCommandIntegration:
    """Integration tests for PartyCommand."""

    async def test_multiple_projects_workflow(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test creating multiple project topics."""
        raw = make_raw_message(12345)

        # Create first project
        ctx1 = make_context(
            ["register", "Project1"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx1)
        assert "Project1" in result.text

        # Create second project
        mock_bot.create_forum_topic = AsyncMock(return_value=MockForumTopic(message_thread_id=200))
        ctx2 = make_context(
            ["register", "Project2"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx2)
        assert "Project2" in result.text

        # List user's topics
        ctx3 = make_context(["topics"], 99999, tmp_config_path, raw_message=raw)
        result = await party_command.handle(ctx3)
        assert "Project1" in result.text
        assert "Project2" in result.text


class TestConfigIntegration:
    """Tests for takopi.toml config integration."""

    async def test_register_adds_project_to_config(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register adds project entry to takopi.toml."""
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is not None
        assert "MyProject" in result.text
        assert "ready to use" in result.text

        # Verify config was updated
        with tmp_config_path.open("rb") as f:
            config = tomli.load(f)

        assert "projects" in config
        assert "party-myproject" in config["projects"]

    async def test_register_project_adds_correct_key(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register ProjectName adds correct project key."""
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "My Cool Project"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is not None
        assert "My Cool Project" in result.text
        assert "ready to use" in result.text

        # Verify config
        with tmp_config_path.open("rb") as f:
            config = tomli.load(f)

        assert "party-my-cool-project" in config["projects"]

    async def test_register_auto_binds_topic(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register auto-binds topic in takopi's state."""
        import json

        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        await party_command.handle(ctx)

        # Verify topic state was updated
        state_path = tmp_config_path.with_name("telegram_topics_state.json")
        with state_path.open() as f:
            state = json.load(f)

        assert "threads" in state
        assert "99999:100" in state["threads"]  # chat_id:thread_id

    async def test_leave_removes_project_from_config(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party leave removes project from takopi.toml."""
        # Register topic
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Verify project was added
        with tmp_config_path.open("rb") as f:
            config = tomli.load(f)
        assert "party-myproject" in config["projects"]

        # Leave the topic
        raw_in_topic = make_raw_message(12345, thread_id=100)
        ctx2 = make_context(
            ["leave"],
            99999,
            tmp_config_path,
            raw_message=raw_in_topic,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx2)

        assert result is not None
        assert "Goodbye" in result.text

        # Verify project was removed
        with tmp_config_path.open("rb") as f:
            config = tomli.load(f)
        assert "party-myproject" not in config.get("projects", {})

    async def test_leave_unbinds_topic(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party leave removes topic binding from takopi's state."""
        import json

        # Register topic
        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Verify topic was bound
        state_path = tmp_config_path.with_name("telegram_topics_state.json")
        with state_path.open() as f:
            state = json.load(f)
        assert "99999:100" in state["threads"]

        # Leave the topic
        raw_in_topic = make_raw_message(12345, thread_id=100)
        ctx2 = make_context(
            ["leave"],
            99999,
            tmp_config_path,
            raw_message=raw_in_topic,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx2)

        # Verify topic binding was removed
        with state_path.open() as f:
            state = json.load(f)
        assert "99999:100" not in state.get("threads", {})

    async def test_register_without_config_file_still_works(
        self,
        party_command: PartyCommand,
        tmp_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register works even without config file."""
        # Use a non-existent config path
        config_path = tmp_path / "nonexistent.toml"

        raw = make_raw_message(12345)
        ctx = make_context(
            ["register", "MyProject"],
            99999,
            config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        # Should still succeed (config update is best-effort)
        assert result is not None
        assert "MyProject" in result.text
        assert "ready to use" in result.text
