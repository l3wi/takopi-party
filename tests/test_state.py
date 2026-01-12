"""Tests for PartyStateStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from takopi_party.state import PartyStateStore


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    """Return a temporary path for state storage."""
    return tmp_path / "party_state.json"


@pytest.fixture
def store(state_path: Path) -> PartyStateStore:
    """Create a fresh state store for each test."""
    return PartyStateStore(state_path)


class TestPartyStateStore:
    """Tests for PartyStateStore."""

    async def test_register_topic(self, store: PartyStateStore) -> None:
        """Test registering a topic."""
        topic = await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        assert topic.thread_id == 100
        assert topic.owner_id == 1001
        assert topic.name == "MyProject"
        assert topic.workspace_path == "/party/my-project"
        assert topic.allowed_users == frozenset()

    async def test_get_topic_by_thread_id(self, store: PartyStateStore) -> None:
        """Test retrieving a topic by thread_id."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        topic = await store.get_topic(100)
        assert topic is not None
        assert topic.name == "MyProject"

        # Non-existent topic returns None
        topic = await store.get_topic(999)
        assert topic is None

    async def test_get_topic_by_thread_validates_chat_id(self, store: PartyStateStore) -> None:
        """Test that get_topic_by_thread checks chat_id."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        # Correct chat_id
        topic = await store.get_topic_by_thread(12345, 100)
        assert topic is not None

        # Wrong chat_id
        topic = await store.get_topic_by_thread(99999, 100)
        assert topic is None

        # None thread_id
        topic = await store.get_topic_by_thread(12345, None)
        assert topic is None

    async def test_can_use_topic(self, store: PartyStateStore) -> None:
        """Test checking if a user can use a topic."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        # Owner can use
        assert await store.can_use_topic(100, 1001) is True

        # Non-owner cannot use initially
        assert await store.can_use_topic(100, 2002) is False

        # Allow a user
        await store.allow_user(100, 2002)
        assert await store.can_use_topic(100, 2002) is True

        # Non-existent topic
        assert await store.can_use_topic(999, 1001) is False

    async def test_get_topics_by_owner(self, store: PartyStateStore) -> None:
        """Test retrieving all topics owned by a user."""
        # Register two topics for user 1001
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="Project1",
            workspace_path="/party/project1",
        )
        await store.register_topic(
            chat_id=12345,
            thread_id=200,
            owner_id=1001,
            name="Project2",
            workspace_path="/party/project2",
        )

        # Register one topic for user 2002
        await store.register_topic(
            chat_id=12345,
            thread_id=300,
            owner_id=2002,
            name="Project3",
            workspace_path="/party/project3",
        )

        user1_topics = await store.get_topics_by_owner(1001)
        assert len(user1_topics) == 2

        user2_topics = await store.get_topics_by_owner(2002)
        assert len(user2_topics) == 1

        unknown_topics = await store.get_topics_by_owner(9999)
        assert len(unknown_topics) == 0

    async def test_topic_name_exists(self, store: PartyStateStore) -> None:
        """Test checking if a topic name already exists."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        # Exact match
        assert await store.topic_name_exists("MyProject") is True

        # Case-insensitive match
        assert await store.topic_name_exists("myproject") is True
        assert await store.topic_name_exists("MYPROJECT") is True

        # Non-existent
        assert await store.topic_name_exists("OtherProject") is False

    async def test_unregister_topic(self, store: PartyStateStore) -> None:
        """Test unregistering a topic."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        # Unregister existing topic
        topic = await store.unregister_topic(100)
        assert topic is not None
        assert topic.name == "MyProject"

        # Verify it's gone
        topic = await store.get_topic(100)
        assert topic is None

        # Unregister non-existent topic
        topic = await store.unregister_topic(999)
        assert topic is None

    async def test_list_topics(self, store: PartyStateStore) -> None:
        """Test listing all topics."""
        # Empty list initially
        topics = await store.list_topics()
        assert topics == []

        # Add some topics
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="Project1",
            workspace_path="/party/project1",
        )
        await store.register_topic(
            chat_id=12345,
            thread_id=200,
            owner_id=2002,
            name="Project2",
            workspace_path="/party/project2",
        )

        topics = await store.list_topics()
        assert len(topics) == 2
        names = {t.name for t in topics}
        assert names == {"Project1", "Project2"}

    async def test_is_party_chat(self, store: PartyStateStore) -> None:
        """Test checking if a chat is the party chat."""
        # Before any registration
        assert await store.is_party_chat(12345) is False

        # After registration
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        assert await store.is_party_chat(12345) is True
        assert await store.is_party_chat(99999) is False

    async def test_chat_id_mismatch_raises(self, store: PartyStateStore) -> None:
        """Test that registering with different chat_id raises error."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="Project1",
            workspace_path="/party/project1",
        )

        with pytest.raises(ValueError, match="chat ID mismatch"):
            await store.register_topic(
                chat_id=99999,  # Different chat_id
                thread_id=200,
                owner_id=2002,
                name="Project2",
                workspace_path="/party/project2",
            )

    async def test_allow_user(self, store: PartyStateStore) -> None:
        """Test allowing a user to access a topic."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        # Allow a new user
        added = await store.allow_user(100, 2002)
        assert added is True

        # Verify user is in allowed_users
        topic = await store.get_topic(100)
        assert topic is not None
        assert 2002 in topic.allowed_users

        # Allow same user again returns False
        added = await store.allow_user(100, 2002)
        assert added is False

        # Allow owner returns False
        added = await store.allow_user(100, 1001)
        assert added is False

    async def test_revoke_user(self, store: PartyStateStore) -> None:
        """Test revoking a user's access to a topic."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )

        # Allow then revoke
        await store.allow_user(100, 2002)
        revoked = await store.revoke_user(100, 2002)
        assert revoked is True

        # Verify user is no longer in allowed_users
        topic = await store.get_topic(100)
        assert topic is not None
        assert 2002 not in topic.allowed_users

        # Revoke non-allowed user returns False
        revoked = await store.revoke_user(100, 3003)
        assert revoked is False

    async def test_allow_user_nonexistent_topic(self, store: PartyStateStore) -> None:
        """Test allowing user on nonexistent topic raises error."""
        with pytest.raises(ValueError, match="not found"):
            await store.allow_user(999, 2002)

    async def test_revoke_user_nonexistent_topic(self, store: PartyStateStore) -> None:
        """Test revoking user on nonexistent topic raises error."""
        with pytest.raises(ValueError, match="not found"):
            await store.revoke_user(999, 2002)

    async def test_persistence(self, state_path: Path) -> None:
        """Test that state persists across store instances."""
        store1 = PartyStateStore(state_path)
        await store1.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            name="MyProject",
            workspace_path="/party/my-project",
        )
        await store1.allow_user(100, 2002)

        # Create new store instance
        store2 = PartyStateStore(state_path)
        topic = await store2.get_topic(100)

        assert topic is not None
        assert topic.name == "MyProject"
        assert topic.owner_id == 1001
        assert 2002 in topic.allowed_users
