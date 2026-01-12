"""Party state storage - manages party members and their topics."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
import msgspec

STATE_VERSION = 1
STATE_FILENAME = "telegram_party_state.json"


@dataclass(frozen=True, slots=True)
class PartyMember:
    """A registered party member with their dedicated topic."""

    user_id: int
    username: str | None
    display_name: str
    thread_id: int
    workspace_path: str
    registered_at: str
    allowed_users: frozenset[int]


class _PartyMemberState(msgspec.Struct, forbid_unknown_fields=False):
    """Serializable state for a party member."""

    user_id: int
    username: str | None
    display_name: str
    thread_id: int
    workspace_path: str
    registered_at: str
    allowed_users: list[int] = msgspec.field(default_factory=list)


class _PartyState(msgspec.Struct, forbid_unknown_fields=False):
    """Root state containing all party members."""

    version: int
    chat_id: int = 0
    members: dict[str, _PartyMemberState] = msgspec.field(default_factory=dict)


def resolve_party_state_path(config_path: Path) -> Path:
    """Resolve the party state file path from config path."""
    return config_path.with_name(STATE_FILENAME)


def _member_key(user_id: int) -> str:
    """Generate storage key for a user."""
    return str(user_id)


def _member_from_state(state: _PartyMemberState) -> PartyMember:
    """Convert serialized state to PartyMember dataclass."""
    return PartyMember(
        user_id=state.user_id,
        username=state.username,
        display_name=state.display_name,
        thread_id=state.thread_id,
        workspace_path=state.workspace_path,
        registered_at=state.registered_at,
        allowed_users=frozenset(state.allowed_users),
    )


def _member_to_state(member: PartyMember) -> _PartyMemberState:
    """Convert PartyMember to serializable state."""
    return _PartyMemberState(
        user_id=member.user_id,
        username=member.username,
        display_name=member.display_name,
        thread_id=member.thread_id,
        workspace_path=member.workspace_path,
        registered_at=member.registered_at,
        allowed_users=sorted(member.allowed_users),
    )


class PartyStateStore:
    """Thread-safe storage for party state with file persistence."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._state = _PartyState(version=STATE_VERSION, chat_id=0, members={})

    @property
    def chat_id(self) -> int:
        """Get the party chat ID.

        Note: This accesses cached state without lock. For authoritative
        checks, use is_party_chat() or get_member_by_thread() instead.
        """
        return self._state.chat_id

    async def get_member(self, user_id: int) -> PartyMember | None:
        """Get party member by user_id."""
        async with self._lock:
            self._reload_locked_if_needed()
            state = self._state.members.get(_member_key(user_id))
            if state is None:
                return None
            return _member_from_state(state)

    async def get_member_by_thread(self, chat_id: int, thread_id: int | None) -> PartyMember | None:
        """Get party member by thread_id.

        Returns None if:
        - thread_id is None (General topic)
        - chat_id doesn't match the party chat
        - No member owns this thread
        """
        if thread_id is None:
            return None
        async with self._lock:
            self._reload_locked_if_needed()
            if self._state.chat_id != chat_id:
                return None
            for state in self._state.members.values():
                if state.thread_id == thread_id:
                    return _member_from_state(state)
            return None

    async def is_party_chat(self, chat_id: int) -> bool:
        """Check if a chat is the party chat."""
        async with self._lock:
            self._reload_locked_if_needed()
            return self._state.chat_id == chat_id and self._state.chat_id != 0

    async def register(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str,
        thread_id: int,
        workspace_path: str,
    ) -> PartyMember:
        """Register a new party member."""
        async with self._lock:
            self._reload_locked_if_needed()

            # Set chat_id if not already set
            if self._state.chat_id == 0:
                self._state.chat_id = chat_id
            elif self._state.chat_id != chat_id:
                raise ValueError(
                    f"Party chat ID mismatch: expected {self._state.chat_id}, got {chat_id}"
                )

            member = PartyMember(
                user_id=user_id,
                username=username,
                display_name=display_name,
                thread_id=thread_id,
                workspace_path=workspace_path,
                registered_at=datetime.now(UTC).isoformat(),
                allowed_users=frozenset(),
            )

            self._state.members[_member_key(user_id)] = _member_to_state(member)
            self._save_locked()
            return member

    async def unregister(self, user_id: int) -> PartyMember | None:
        """Unregister a party member, returns the member if found."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _member_key(user_id)
            state = self._state.members.pop(key, None)
            if state is None:
                return None
            self._save_locked()
            return _member_from_state(state)

    async def allow_user(self, owner_id: int, guest_id: int) -> bool:
        """Allow a guest user in owner's topic. Returns True if successful."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _member_key(owner_id)
            state = self._state.members.get(key)
            if state is None:
                return False

            if guest_id in state.allowed_users:
                return True  # Already allowed

            member = _member_from_state(state)
            updated = PartyMember(
                user_id=member.user_id,
                username=member.username,
                display_name=member.display_name,
                thread_id=member.thread_id,
                workspace_path=member.workspace_path,
                registered_at=member.registered_at,
                allowed_users=member.allowed_users | {guest_id},
            )

            self._state.members[key] = _member_to_state(updated)
            self._save_locked()
            return True

    async def revoke_user(self, owner_id: int, guest_id: int) -> bool:
        """Revoke a guest user from owner's topic. Returns True if successful."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _member_key(owner_id)
            state = self._state.members.get(key)
            if state is None:
                return False

            if guest_id not in state.allowed_users:
                return True  # Already not allowed

            member = _member_from_state(state)
            updated = PartyMember(
                user_id=member.user_id,
                username=member.username,
                display_name=member.display_name,
                thread_id=member.thread_id,
                workspace_path=member.workspace_path,
                registered_at=member.registered_at,
                allowed_users=member.allowed_users - {guest_id},
            )

            self._state.members[key] = _member_to_state(updated)
            self._save_locked()
            return True

    async def list_members(self) -> list[PartyMember]:
        """List all registered party members."""
        async with self._lock:
            self._reload_locked_if_needed()
            return [_member_from_state(state) for state in self._state.members.values()]

    async def update_username(self, user_id: int, username: str | None) -> bool:
        """Update a member's username (for tracking @username changes)."""
        async with self._lock:
            self._reload_locked_if_needed()
            key = _member_key(user_id)
            state = self._state.members.get(key)
            if state is None:
                return False

            if state.username == username:
                return True  # No change needed

            member = _member_from_state(state)
            updated = PartyMember(
                user_id=member.user_id,
                username=username,
                display_name=member.display_name,
                thread_id=member.thread_id,
                workspace_path=member.workspace_path,
                registered_at=member.registered_at,
                allowed_users=member.allowed_users,
            )

            self._state.members[key] = _member_to_state(updated)
            self._save_locked()
            return True

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
            self._state = _PartyState(version=STATE_VERSION, chat_id=0, members={})
            return
        try:
            payload = msgspec.json.decode(self._path.read_bytes(), type=_PartyState)
        except Exception:
            self._state = _PartyState(version=STATE_VERSION, chat_id=0, members={})
            return
        if payload.version != STATE_VERSION:
            self._state = _PartyState(version=STATE_VERSION, chat_id=0, members={})
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
