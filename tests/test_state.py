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

    async def test_register_personal_topic(self, store: PartyStateStore) -> None:
        """Test registering a personal topic."""
        topic = await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        assert topic.thread_id == 100
        assert topic.owner_id == 1001
        assert topic.owner_username == "alice"
        assert topic.name == "Alice"
        assert topic.workspace_path == "/party/1001"
        assert topic.is_personal is True
        assert topic.allowed_users == frozenset()

    async def test_register_project_topic(self, store: PartyStateStore) -> None:
        """Test registering a project topic."""
        topic = await store.register_topic(
            chat_id=12345,
            thread_id=200,
            owner_id=1001,
            owner_username="alice",
            name="MyProject",
            workspace_path="/party/my-project",
            is_personal=False,
        )

        assert topic.thread_id == 200
        assert topic.name == "MyProject"
        assert topic.is_personal is False

    async def test_get_topic_by_thread_id(self, store: PartyStateStore) -> None:
        """Test retrieving a topic by thread_id."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        topic = await store.get_topic(100)
        assert topic is not None
        assert topic.name == "Alice"

        # Non-existent topic returns None
        topic = await store.get_topic(999)
        assert topic is None

    async def test_get_topic_by_thread_validates_chat_id(self, store: PartyStateStore) -> None:
        """Test that get_topic_by_thread checks chat_id."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
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

    async def test_get_personal_topic(self, store: PartyStateStore) -> None:
        """Test retrieving a user's personal topic."""
        # Register personal topic
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        # Register project topic for same user
        await store.register_topic(
            chat_id=12345,
            thread_id=200,
            owner_id=1001,
            owner_username="alice",
            name="MyProject",
            workspace_path="/party/my-project",
            is_personal=False,
        )

        personal = await store.get_personal_topic(1001)
        assert personal is not None
        assert personal.is_personal is True
        assert personal.name == "Alice"

        # User without personal topic
        personal = await store.get_personal_topic(9999)
        assert personal is None

    async def test_get_topics_by_owner(self, store: PartyStateStore) -> None:
        """Test retrieving all topics owned by a user."""
        # Register two topics for alice
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )
        await store.register_topic(
            chat_id=12345,
            thread_id=200,
            owner_id=1001,
            owner_username="alice",
            name="MyProject",
            workspace_path="/party/my-project",
            is_personal=False,
        )

        # Register one topic for bob
        await store.register_topic(
            chat_id=12345,
            thread_id=300,
            owner_id=2002,
            owner_username="bob",
            name="Bob",
            workspace_path="/party/2002",
            is_personal=True,
        )

        alice_topics = await store.get_topics_by_owner(1001)
        assert len(alice_topics) == 2

        bob_topics = await store.get_topics_by_owner(2002)
        assert len(bob_topics) == 1

        unknown_topics = await store.get_topics_by_owner(9999)
        assert len(unknown_topics) == 0

    async def test_topic_name_exists(self, store: PartyStateStore) -> None:
        """Test checking if a topic name already exists."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="MyProject",
            workspace_path="/party/my-project",
            is_personal=False,
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
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        # Unregister existing topic
        topic = await store.unregister_topic(100)
        assert topic is not None
        assert topic.name == "Alice"

        # Verify it's gone
        topic = await store.get_topic(100)
        assert topic is None

        # Unregister non-existent topic
        topic = await store.unregister_topic(999)
        assert topic is None

    async def test_allow_user(self, store: PartyStateStore) -> None:
        """Test allowing a user in a topic."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        # Allow a user
        success = await store.allow_user(100, 2002)
        assert success is True

        topic = await store.get_topic(100)
        assert topic is not None
        assert 2002 in topic.allowed_users

        # Allow same user again (idempotent)
        success = await store.allow_user(100, 2002)
        assert success is True

        # Allow on non-existent topic
        success = await store.allow_user(999, 2002)
        assert success is False

    async def test_revoke_user(self, store: PartyStateStore) -> None:
        """Test revoking a user from a topic."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        # Allow then revoke
        await store.allow_user(100, 2002)
        success = await store.revoke_user(100, 2002)
        assert success is True

        topic = await store.get_topic(100)
        assert topic is not None
        assert 2002 not in topic.allowed_users

        # Revoke user not in list (idempotent)
        success = await store.revoke_user(100, 3003)
        assert success is True

        # Revoke on non-existent topic
        success = await store.revoke_user(999, 2002)
        assert success is False

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
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )
        await store.register_topic(
            chat_id=12345,
            thread_id=200,
            owner_id=2002,
            owner_username="bob",
            name="Bob",
            workspace_path="/party/2002",
            is_personal=True,
        )

        topics = await store.list_topics()
        assert len(topics) == 2
        names = {t.name for t in topics}
        assert names == {"Alice", "Bob"}

    async def test_is_party_chat(self, store: PartyStateStore) -> None:
        """Test checking if a chat is the party chat."""
        # Before any registration
        assert await store.is_party_chat(12345) is False

        # After registration
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        assert await store.is_party_chat(12345) is True
        assert await store.is_party_chat(99999) is False

    async def test_chat_id_mismatch_raises(self, store: PartyStateStore) -> None:
        """Test that registering with different chat_id raises error."""
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        with pytest.raises(ValueError, match="chat ID mismatch"):
            await store.register_topic(
                chat_id=99999,  # Different chat_id
                thread_id=200,
                owner_id=2002,
                owner_username="bob",
                name="Bob",
                workspace_path="/party/2002",
                is_personal=True,
            )

    async def test_persistence(self, state_path: Path) -> None:
        """Test that state persists across store instances."""
        store1 = PartyStateStore(state_path)
        await store1.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )

        # Create new store instance
        store2 = PartyStateStore(state_path)
        topic = await store2.get_topic(100)

        assert topic is not None
        assert topic.name == "Alice"
        assert topic.owner_id == 1001

    async def test_update_owner_username(self, store: PartyStateStore) -> None:
        """Test updating owner username across topics."""
        # Register two topics for same owner
        await store.register_topic(
            chat_id=12345,
            thread_id=100,
            owner_id=1001,
            owner_username="alice",
            name="Alice",
            workspace_path="/party/1001",
            is_personal=True,
        )
        await store.register_topic(
            chat_id=12345,
            thread_id=200,
            owner_id=1001,
            owner_username="alice",
            name="Project",
            workspace_path="/party/project",
            is_personal=False,
        )

        # Update username
        count = await store.update_owner_username(1001, "alice_new")
        assert count == 2

        # Verify both topics updated
        topics = await store.get_topics_by_owner(1001)
        for topic in topics:
            assert topic.owner_username == "alice_new"

        # Update non-existent user
        count = await store.update_owner_username(9999, "nobody")
        assert count == 0
