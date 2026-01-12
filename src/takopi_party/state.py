"""Party state storage - manages party topics and their members."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
import msgspec

STATE_VERSION = 4
STATE_FILENAME = "telegram_party_state.json"


@dataclass(frozen=True, slots=True)
class PartyTopic:
    """A party topic with its owner and workspace."""

    thread_id: int  # Primary key
    owner_id: int  # User who created/owns it
    name: str  # Topic display name
    workspace_path: str
    allowed_users: frozenset[int]  # User IDs allowed to use this topic
    registered_at: str


class _PartyTopicState(msgspec.Struct, forbid_unknown_fields=False):
    """Serializable state for a party topic."""

    thread_id: int
    owner_id: int
    name: str
    workspace_path: str
    allowed_users: list[int]  # Stored as list, converted to frozenset
    registered_at: str


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
        name=state.name,
        workspace_path=state.workspace_path,
        allowed_users=frozenset(state.allowed_users),
        registered_at=state.registered_at,
    )


def _topic_to_state(topic: PartyTopic) -> _PartyTopicState:
    """Convert PartyTopic to serializable state."""
    return _PartyTopicState(
        thread_id=topic.thread_id,
        owner_id=topic.owner_id,
        name=topic.name,
        workspace_path=topic.workspace_path,
        allowed_users=sorted(topic.allowed_users),
        registered_at=topic.registered_at,
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

    async def can_use_topic(self, thread_id: int, user_id: int) -> bool:
        """Check if a user can use a topic (owner or allowed)."""
        async with self._lock:
            self._reload_locked_if_needed()
            state = self._state.topics.get(_topic_key(thread_id))
            if state is None:
                return False
            return state.owner_id == user_id or user_id in state.allowed_users

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
        name: str,
        workspace_path: str,
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
                name=name,
                workspace_path=workspace_path,
                allowed_users=frozenset(),
                registered_at=datetime.now(UTC).isoformat(),
            )

            self._state.topics[_topic_key(thread_id)] = _topic_to_state(topic)
            self._save_locked()
            return topic

    async def allow_user(self, thread_id: int, user_id: int) -> bool:
        """Allow a user to use a topic. Returns True if added, False if already allowed."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _topic_key(thread_id)
            state = self._state.topics.get(key)
            if state is None:
                raise ValueError(f"Topic {thread_id} not found")
            if user_id in state.allowed_users or user_id == state.owner_id:
                return False
            # Create new state with updated allowed_users
            new_allowed = list(state.allowed_users) + [user_id]
            self._state.topics[key] = _PartyTopicState(
                thread_id=state.thread_id,
                owner_id=state.owner_id,
                name=state.name,
                workspace_path=state.workspace_path,
                allowed_users=new_allowed,
                registered_at=state.registered_at,
            )
            self._save_locked()
            return True

    async def revoke_user(self, thread_id: int, user_id: int) -> bool:
        """Revoke a user's access to a topic. Returns True if removed, False if not found."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _topic_key(thread_id)
            state = self._state.topics.get(key)
            if state is None:
                raise ValueError(f"Topic {thread_id} not found")
            if user_id not in state.allowed_users:
                return False
            # Create new state with updated allowed_users
            new_allowed = [uid for uid in state.allowed_users if uid != user_id]
            self._state.topics[key] = _PartyTopicState(
                thread_id=state.thread_id,
                owner_id=state.owner_id,
                name=state.name,
                workspace_path=state.workspace_path,
                allowed_users=new_allowed,
                registered_at=state.registered_at,
            )
            self._save_locked()
            return True

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

    async def list_topics(self) -> list[PartyTopic]:
        """List all registered party topics."""
        async with self._lock:
            self._reload_locked_if_needed()
            return [_topic_from_state(state) for state in self._state.topics.values()]

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
