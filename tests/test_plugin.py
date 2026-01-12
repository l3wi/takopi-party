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
    _extract_sender_info,
    _get_thread_id_from_message,
    _parse_username_arg,
)


@dataclass
class MockMessage:
    """Mock message for testing."""

    channel_id: int
    raw: dict[str, Any] | None = None
    thread_id: int | None = None


@dataclass
class MockForumTopic:
    """Mock forum topic result."""

    message_thread_id: int


def make_raw_message(
    sender_id: int,
    username: str | None = None,
    first_name: str = "Test",
    last_name: str = "",
    thread_id: int | None = None,
) -> dict[str, Any]:
    """Create a mock raw Telegram message."""
    msg: dict[str, Any] = {
        "from": {
            "id": sender_id,
            "first_name": first_name,
            "last_name": last_name,
        }
    }
    if username:
        msg["from"]["username"] = username
    if thread_id is not None:
        msg["message_thread_id"] = thread_id
    return msg


def make_context(
    args: list[str],
    chat_id: int,
    config_path: Path,
    raw_message: dict[str, Any] | None = None,
    bot: Any = None,
    workspace_base: str = "/party",
) -> MagicMock:
    """Create a mock CommandContext."""
    # Extract thread_id from raw_message if present
    thread_id = raw_message.get("message_thread_id") if raw_message else None

    ctx = MagicMock()
    ctx.args = args
    ctx.message = MockMessage(channel_id=chat_id, raw=raw_message, thread_id=thread_id)
    ctx.config_path = config_path
    ctx.plugin_config = {
        "raw_message": raw_message,
        "bot": bot,
        "workspace_base": workspace_base,
    }
    return ctx


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_extract_sender_info_full(self) -> None:
        """Test extracting full sender info."""
        raw = make_raw_message(12345, "alice", "Alice", "Smith")
        sender_id, username, display_name = _extract_sender_info(raw)

        assert sender_id == 12345
        assert username == "alice"
        assert display_name == "Alice Smith"

    def test_extract_sender_info_no_username(self) -> None:
        """Test extracting sender info without username."""
        raw = make_raw_message(12345, None, "Alice")
        sender_id, username, display_name = _extract_sender_info(raw)

        assert sender_id == 12345
        assert username is None
        assert display_name == "Alice"

    def test_extract_sender_info_no_name(self) -> None:
        """Test extracting sender info without name (uses username)."""
        raw: dict[str, Any] = {"from": {"id": 12345, "username": "alice"}}
        sender_id, username, display_name = _extract_sender_info(raw)

        assert sender_id == 12345
        assert username == "alice"
        assert display_name == "alice"

    def test_extract_sender_info_none(self) -> None:
        """Test extracting sender info from None."""
        sender_id, username, display_name = _extract_sender_info(None)
        assert sender_id is None
        assert username is None
        assert display_name is None

    def test_extract_sender_info_invalid(self) -> None:
        """Test extracting sender info from invalid data."""
        sender_id, username, display_name = _extract_sender_info({})
        assert sender_id is None

        sender_id, username, display_name = _extract_sender_info({"from": "invalid"})
        assert sender_id is None

    def test_parse_username_arg_with_at(self) -> None:
        """Test parsing username with @ prefix."""
        assert _parse_username_arg("@alice") == "alice"
        assert _parse_username_arg("  @bob  ") == "bob"

    def test_parse_username_arg_without_at(self) -> None:
        """Test parsing username without @ prefix."""
        assert _parse_username_arg("alice") == "alice"

    def test_parse_username_arg_empty(self) -> None:
        """Test parsing empty username."""
        assert _parse_username_arg("") is None
        assert _parse_username_arg("  ") is None
        assert _parse_username_arg("@") is None

    def test_get_thread_id_from_message(self) -> None:
        """Test extracting thread_id from message."""
        raw = make_raw_message(12345, thread_id=100)
        assert _get_thread_id_from_message(raw) == 100

        raw = make_raw_message(12345)
        assert _get_thread_id_from_message(raw) is None

        assert _get_thread_id_from_message(None) is None


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
    """Create a fresh PartyCommand for each test."""
    return PartyCommand()


@pytest.fixture
def mock_bot() -> AsyncMock:
    """Create a mock bot client."""
    bot = AsyncMock()
    bot.create_forum_topic = AsyncMock(return_value=MockForumTopic(message_thread_id=100))
    return bot


class TestPartyCommand:
    """Tests for PartyCommand."""

    async def test_handle_help(self, party_command: PartyCommand, tmp_config_path: Path) -> None:
        """Test /party help command."""
        ctx = make_context(["help"], 12345, tmp_config_path)
        result = await party_command.handle(ctx)

        assert result is not None
        assert "Party Mode Commands" in result.text
        assert "/party register" in result.text

    async def test_handle_default_is_help(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party with no args shows help."""
        ctx = make_context([], 12345, tmp_config_path)
        result = await party_command.handle(ctx)

        assert result is not None
        assert "Party Mode Commands" in result.text

    async def test_handle_register_personal(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register creates personal topic."""
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
            12345,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is not None
        assert "Welcome to the party" in result.text
        assert "Alice" in result.text
        mock_bot.create_forum_topic.assert_called_once()

    async def test_handle_register_personal_twice_fails(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register twice fails for personal topic."""
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
            12345,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        # First registration
        await party_command.handle(ctx)

        # Second registration should fail
        mock_bot.create_forum_topic.return_value = MockForumTopic(message_thread_id=200)
        result = await party_command.handle(ctx)

        assert result is not None
        assert "already have a personal topic" in result.text

    async def test_handle_register_project(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register ProjectName creates project topic."""
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register", "MyProject"],
            12345,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        assert result is not None
        assert "Project" in result.text
        assert "MyProject" in result.text

    async def test_handle_register_project_duplicate_name_fails(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register with duplicate project name fails."""
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register", "MyProject"],
            12345,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        # First registration
        await party_command.handle(ctx)

        # Second with same name (even different case)
        mock_bot.create_forum_topic.return_value = MockForumTopic(message_thread_id=200)
        ctx2 = make_context(
            ["register", "myproject"],  # Different case
            12345,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx2)

        assert result is not None
        assert "already exists" in result.text

    async def test_handle_register_no_sender(
        self, party_command: PartyCommand, tmp_config_path: Path, mock_bot: AsyncMock
    ) -> None:
        """Test /party register with no sender info."""
        ctx = make_context(
            ["register"],
            12345,
            tmp_config_path,
            raw_message=None,
            bot=mock_bot,
        )

        result = await party_command.handle(ctx)

        assert result is not None
        assert "Could not identify sender" in result.text

    async def test_handle_list_empty(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party list with no topics."""
        ctx = make_context(["list"], 12345, tmp_config_path)
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
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
            12345,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Now list
        ctx2 = make_context(["list"], 12345, tmp_config_path)
        result = await party_command.handle(ctx2)

        assert result is not None
        assert "Party Topics" in result.text
        assert "Alice" in result.text

    async def test_handle_topics_empty(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party topics with no owned topics."""
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(["topics"], 12345, tmp_config_path, raw_message=raw)
        result = await party_command.handle(ctx)

        assert result is not None
        assert "don't have any topics" in result.text

    async def test_handle_allow_not_in_topic(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party allow outside a topic."""
        raw = make_raw_message(12345, "alice", "Alice")  # No thread_id
        ctx = make_context(
            ["allow", "@bob"],
            12345,
            tmp_config_path,
            raw_message=raw,
        )
        result = await party_command.handle(ctx)

        assert result is not None
        assert "must be used inside a party topic" in result.text

    async def test_handle_allow_not_owner(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party allow by non-owner."""
        # Alice registers topic
        raw_alice = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
            99999,
            tmp_config_path,
            raw_message=raw_alice,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Bob tries to allow in Alice's topic
        raw_bob = make_raw_message(67890, "bob", "Bob", thread_id=100)
        ctx2 = make_context(
            ["allow", "@charlie"],
            99999,
            tmp_config_path,
            raw_message=raw_bob,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx2)

        assert result is not None
        assert "Only the topic owner" in result.text

    async def test_handle_revoke_not_in_topic(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party revoke outside a topic."""
        raw = make_raw_message(12345, "alice", "Alice")  # No thread_id
        ctx = make_context(
            ["revoke", "@bob"],
            12345,
            tmp_config_path,
            raw_message=raw,
        )
        result = await party_command.handle(ctx)

        assert result is not None
        assert "must be used inside a party topic" in result.text

    async def test_handle_leave_not_in_topic(
        self, party_command: PartyCommand, tmp_config_path: Path
    ) -> None:
        """Test /party leave outside a topic."""
        raw = make_raw_message(12345, "alice", "Alice")  # No thread_id
        ctx = make_context(
            ["leave"],
            12345,
            tmp_config_path,
            raw_message=raw,
        )
        result = await party_command.handle(ctx)

        assert result is not None
        assert "must be used inside a party topic" in result.text

    async def test_handle_leave_success(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party leave successfully closes topic."""
        # Register topic
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Leave from within the topic
        raw_in_topic = make_raw_message(12345, "alice", "Alice", thread_id=100)
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
        assert "archived" in result.text.lower()


class TestPartyCommandIntegration:
    """Integration tests for full workflows."""

    async def test_personal_then_project_workflow(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test user can create personal topic then project topics."""
        raw = make_raw_message(12345, "alice", "Alice")

        # Create personal topic
        ctx = make_context(
            ["register"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx)
        assert "Welcome to the party" in result.text

        # Create first project
        mock_bot.create_forum_topic.return_value = MockForumTopic(message_thread_id=200)
        ctx2 = make_context(
            ["register", "Project Alpha"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx2)
        assert "Project" in result.text
        assert "Project Alpha" in result.text

        # Create second project
        mock_bot.create_forum_topic.return_value = MockForumTopic(message_thread_id=300)
        ctx3 = make_context(
            ["register", "Project Beta"],
            99999,
            tmp_config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx3)
        assert "Project Beta" in result.text

        # List user's topics
        ctx4 = make_context(["topics"], 99999, tmp_config_path, raw_message=raw)
        result = await party_command.handle(ctx4)
        assert "Your Topics" in result.text
        assert "Alice" in result.text
        assert "Project Alpha" in result.text
        assert "Project Beta" in result.text

    async def test_allow_and_revoke_workflow(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test allowing and revoking users."""
        # Alice registers
        raw_alice = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
            99999,
            tmp_config_path,
            raw_message=raw_alice,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx)

        # Bob registers
        mock_bot.create_forum_topic.return_value = MockForumTopic(message_thread_id=200)
        raw_bob = make_raw_message(67890, "bob", "Bob")
        ctx2 = make_context(
            ["register"],
            99999,
            tmp_config_path,
            raw_message=raw_bob,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )
        await party_command.handle(ctx2)

        # Alice allows Bob in her topic
        raw_alice_in_topic = make_raw_message(12345, "alice", "Alice", thread_id=100)
        ctx3 = make_context(
            ["allow", "@bob"],
            99999,
            tmp_config_path,
            raw_message=raw_alice_in_topic,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx3)
        assert "@bob can now interact" in result.text

        # Alice revokes Bob
        ctx4 = make_context(
            ["revoke", "@bob"],
            99999,
            tmp_config_path,
            raw_message=raw_alice_in_topic,
            workspace_base=str(workspace_base),
        )
        result = await party_command.handle(ctx4)
        assert "access to this topic has been revoked" in result.text


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
        raw = make_raw_message(12345, "alice", "Alice")
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
        assert "Welcome to the party" in result.text
        assert "ready to use" in result.text

        # Verify config was updated
        with tmp_config_path.open("rb") as f:
            config = tomli.load(f)

        assert "projects" in config
        assert "party-user-alice" in config["projects"]
        assert config["projects"]["party-user-alice"]["default_engine"] == "claude"

    async def test_register_project_adds_correct_key(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party register ProjectName adds correct project key."""
        raw = make_raw_message(12345, "alice", "Alice")
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

        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
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
        assert state["threads"]["99999:100"]["context"]["project"] == "party-user-alice"

    async def test_leave_removes_project_from_config(
        self,
        party_command: PartyCommand,
        tmp_config_path: Path,
        workspace_base: Path,
        mock_bot: AsyncMock,
    ) -> None:
        """Test /party leave removes project from takopi.toml."""
        # Register topic
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
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
        assert "party-user-alice" in config["projects"]

        # Leave the topic
        raw_in_topic = make_raw_message(12345, "alice", "Alice", thread_id=100)
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
        assert "party-user-alice" not in config.get("projects", {})

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
        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
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
        raw_in_topic = make_raw_message(12345, "alice", "Alice", thread_id=100)
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

        raw = make_raw_message(12345, "alice", "Alice")
        ctx = make_context(
            ["register"],
            99999,
            config_path,
            raw_message=raw,
            bot=mock_bot,
            workspace_base=str(workspace_base),
        )

        result = await party_command.handle(ctx)

        # Should still succeed (config update is best-effort)
        assert result is not None
        assert "Welcome to the party" in result.text
        assert "ready to use" in result.text
