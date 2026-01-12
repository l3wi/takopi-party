"""Party state storage - manages party topics and their members."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
import msgspec

STATE_VERSION = 2
STATE_FILENAME = "telegram_party_state.json"


@dataclass(frozen=True, slots=True)
class PartyTopic:
    """A party topic with its owner and workspace."""

    thread_id: int  # Primary key
    owner_id: int  # User who created/owns it
    owner_username: str | None
    name: str  # Topic display name
    workspace_path: str
    is_personal: bool  # True = personal topic, False = named project
    registered_at: str
    allowed_users: frozenset[int]


class _PartyTopicState(msgspec.Struct, forbid_unknown_fields=False):
    """Serializable state for a party topic."""

    thread_id: int
    owner_id: int
    owner_username: str | None
    name: str
    workspace_path: str
    is_personal: bool
    registered_at: str
    allowed_users: list[int] = msgspec.field(default_factory=list)


class _PartyState(msgspec.Struct, forbid_unknown_fields=False):
    """Root state containing all party topics."""

    version: int
    chat_id: int = 0
    topics: dict[str, _PartyTopicState] = msgspec.field(default_factory=dict)


def resolve_party_state_path(config_path: Path) -> Path:
    """Resolve the party state file path from config path."""
    return config_path.with_name(STATE_FILENAME)


def _topic_key(thread_id: int) -> str:
    """Generate storage key for a topic."""
    return str(thread_id)


def _topic_from_state(state: _PartyTopicState) -> PartyTopic:
    """Convert serialized state to PartyTopic dataclass."""
    return PartyTopic(
        thread_id=state.thread_id,
        owner_id=state.owner_id,
        owner_username=state.owner_username,
        name=state.name,
        workspace_path=state.workspace_path,
        is_personal=state.is_personal,
        registered_at=state.registered_at,
        allowed_users=frozenset(state.allowed_users),
    )


def _topic_to_state(topic: PartyTopic) -> _PartyTopicState:
    """Convert PartyTopic to serializable state."""
    return _PartyTopicState(
        thread_id=topic.thread_id,
        owner_id=topic.owner_id,
        owner_username=topic.owner_username,
        name=topic.name,
        workspace_path=topic.workspace_path,
        is_personal=topic.is_personal,
        registered_at=topic.registered_at,
        allowed_users=sorted(topic.allowed_users),
    )


class PartyStateStore:
    """Thread-safe storage for party state with file persistence."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._state = _PartyState(version=STATE_VERSION, chat_id=0, topics={})

    @property
    def chat_id(self) -> int:
        """Get the party chat ID.

        Note: This accesses cached state without lock. For authoritative
        checks, use is_party_chat() or get_topic_by_thread() instead.
        """
        return self._state.chat_id

    async def get_topic(self, thread_id: int) -> PartyTopic | None:
        """Get party topic by thread_id."""
        async with self._lock:
            self._reload_locked_if_needed()
            state = self._state.topics.get(_topic_key(thread_id))
            if state is None:
                return None
            return _topic_from_state(state)

    async def get_personal_topic(self, user_id: int) -> PartyTopic | None:
        """Get a user's personal topic (if they have one)."""
        async with self._lock:
            self._reload_locked_if_needed()
            for state in self._state.topics.values():
                if state.owner_id == user_id and state.is_personal:
                    return _topic_from_state(state)
            return None

    async def get_topics_by_owner(self, user_id: int) -> list[PartyTopic]:
        """Get all topics owned by a user."""
        async with self._lock:
            self._reload_locked_if_needed()
            return [
                _topic_from_state(state)
                for state in self._state.topics.values()
                if state.owner_id == user_id
            ]

    async def get_topic_by_thread(self, chat_id: int, thread_id: int | None) -> PartyTopic | None:
        """Get party topic by thread_id.

        Returns None if:
        - thread_id is None (General topic)
        - chat_id doesn't match the party chat
        - No topic exists with this thread_id
        """
        if thread_id is None:
            return None
        async with self._lock:
            self._reload_locked_if_needed()
            if self._state.chat_id != chat_id:
                return None
            state = self._state.topics.get(_topic_key(thread_id))
            if state is None:
                return None
            return _topic_from_state(state)

    async def is_party_chat(self, chat_id: int) -> bool:
        """Check if a chat is the party chat."""
        async with self._lock:
            self._reload_locked_if_needed()
            return self._state.chat_id == chat_id and self._state.chat_id != 0

    async def topic_name_exists(self, name: str) -> bool:
        """Check if a topic with the given name already exists."""
        async with self._lock:
            self._reload_locked_if_needed()
            name_lower = name.lower()
            return any(state.name.lower() == name_lower for state in self._state.topics.values())

    async def register_topic(
        self,
        chat_id: int,
        thread_id: int,
        owner_id: int,
        owner_username: str | None,
        name: str,
        workspace_path: str,
        is_personal: bool,
    ) -> PartyTopic:
        """Register a new party topic."""
        async with self._lock:
            self._reload_locked_if_needed()

            # Set chat_id if not already set
            if self._state.chat_id == 0:
                self._state.chat_id = chat_id
            elif self._state.chat_id != chat_id:
                raise ValueError(
                    f"Party chat ID mismatch: expected {self._state.chat_id}, got {chat_id}"
                )

            topic = PartyTopic(
                thread_id=thread_id,
                owner_id=owner_id,
                owner_username=owner_username,
                name=name,
                workspace_path=workspace_path,
                is_personal=is_personal,
                registered_at=datetime.now(UTC).isoformat(),
                allowed_users=frozenset(),
            )

            self._state.topics[_topic_key(thread_id)] = _topic_to_state(topic)
            self._save_locked()
            return topic

    async def unregister_topic(self, thread_id: int) -> PartyTopic | None:
        """Unregister a topic, returns the topic if found."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _topic_key(thread_id)
            state = self._state.topics.pop(key, None)
            if state is None:
                return None
            self._save_locked()
            return _topic_from_state(state)

    async def allow_user(self, thread_id: int, guest_id: int) -> bool:
        """Allow a guest user in a topic. Returns True if successful."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _topic_key(thread_id)
            state = self._state.topics.get(key)
            if state is None:
                return False

            if guest_id in state.allowed_users:
                return True  # Already allowed

            topic = _topic_from_state(state)
            updated = PartyTopic(
                thread_id=topic.thread_id,
                owner_id=topic.owner_id,
                owner_username=topic.owner_username,
                name=topic.name,
                workspace_path=topic.workspace_path,
                is_personal=topic.is_personal,
                registered_at=topic.registered_at,
                allowed_users=topic.allowed_users | {guest_id},
            )

            self._state.topics[key] = _topic_to_state(updated)
            self._save_locked()
            return True

    async def revoke_user(self, thread_id: int, guest_id: int) -> bool:
        """Revoke a guest user from a topic. Returns True if successful."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _topic_key(thread_id)
            state = self._state.topics.get(key)
            if state is None:
                return False

            if guest_id not in state.allowed_users:
                return True  # Already not allowed

            topic = _topic_from_state(state)
            updated = PartyTopic(
                thread_id=topic.thread_id,
                owner_id=topic.owner_id,
                owner_username=topic.owner_username,
                name=topic.name,
                workspace_path=topic.workspace_path,
                is_personal=topic.is_personal,
                registered_at=topic.registered_at,
                allowed_users=topic.allowed_users - {guest_id},
            )

            self._state.topics[key] = _topic_to_state(updated)
            self._save_locked()
            return True

    async def list_topics(self) -> list[PartyTopic]:
        """List all registered party topics."""
        async with self._lock:
            self._reload_locked_if_needed()
            return [_topic_from_state(state) for state in self._state.topics.values()]

    async def update_owner_username(self, owner_id: int, username: str | None) -> int:
        """Update owner username across all topics they own.

        Returns count of topics updated.
        """
        async with self._lock:
            self._reload_locked_if_needed()
            count = 0
            for key, state in list(self._state.topics.items()):
                if state.owner_id == owner_id and state.owner_username != username:
                    topic = _topic_from_state(state)
                    updated = PartyTopic(
                        thread_id=topic.thread_id,
                        owner_id=topic.owner_id,
                        owner_username=username,
                        name=topic.name,
                        workspace_path=topic.workspace_path,
                        is_personal=topic.is_personal,
                        registered_at=topic.registered_at,
                        allowed_users=topic.allowed_users,
                    )
                    self._state.topics[key] = _topic_to_state(updated)
                    count += 1

            if count > 0:
                self._save_locked()
            return count

    # --- Private methods ---

    def _stat_mtime_ns(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _reload_locked_if_needed(self) -> None:
        current = self._stat_mtime_ns()
        if self._loaded and current == self._mtime_ns:
            return
        self._load_locked()

    def _load_locked(self) -> None:
        self._loaded = True
        self._mtime_ns = self._stat_mtime_ns()
        if self._mtime_ns is None:
            self._state = _PartyState(version=STATE_VERSION, chat_id=0, topics={})
            return
        try:
            payload = msgspec.json.decode(self._path.read_bytes(), type=_PartyState)
        except Exception:
            self._state = _PartyState(version=STATE_VERSION, chat_id=0, topics={})
            return
        if payload.version != STATE_VERSION:
            self._state = _PartyState(version=STATE_VERSION, chat_id=0, topics={})
            return
        self._state = payload

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = msgspec.to_builtins(self._state)
        tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, self._path)
        self._mtime_ns = self._stat_mtime_ns()
