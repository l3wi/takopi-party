"""Party command plugin - manages multi-user private topics."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from takopi.commands import CommandContext, CommandResult
from takopi.config import ConfigError

from .config import (
    add_party_project,
    bind_topic_to_project,
    get_party_project_key,
    remove_party_project,
    unbind_topic,
)
from .state import PartyStateStore, resolve_party_state_path
from .workspace import PartyWorkspaceManager, WorkspaceError

DEFAULT_WORKSPACE_BASE = "/root/dev/party"


def _extract_mentioned_user_id(raw: dict[str, Any] | None) -> int | None:
    """Extract user ID from a text_mention entity in the message.

    When a user @mentions someone, Telegram includes a text_mention entity
    with the mentioned user's full User object (including their ID).

    Returns:
        The mentioned user's ID, or None if no text_mention found.
    """
    if raw is None:
        return None

    entities = raw.get("entities")
    if not isinstance(entities, list):
        return None

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") == "text_mention":
            user = entity.get("user")
            if isinstance(user, dict):
                user_id = user.get("id")
                if isinstance(user_id, int):
                    return user_id

    return None


def _get_thread_id(ctx: CommandContext) -> int | None:
    """Get thread_id from context.

    Returns the thread_id if the message is in a topic, None otherwise.
    """
    # Try ctx.message.thread_id first (available in takopi's MessageRef)
    if hasattr(ctx.message, "thread_id") and isinstance(ctx.message.thread_id, int):
        return ctx.message.thread_id
    # Fallback to raw message extraction
    raw = ctx.message.raw
    if raw is None:
        return None
    thread_id = raw.get("message_thread_id")
    if isinstance(thread_id, int):
        return thread_id
    return None


class PartyCommand:
    """Command to manage party mode - private topics for multiple users.

    This plugin enables multi-user access control for Telegram forum topics.
    Users create topics manually in Telegram, then register them with the
    plugin to enable workspace management and access control.

    The first user to run /party register in a topic becomes the owner.
    """

    id = "party"
    description = "Manage party mode - private topics for multiple users"

    def __init__(self) -> None:
        self._store: PartyStateStore | None = None
        self._workspace: PartyWorkspaceManager | None = None

    def _get_store(self, ctx: CommandContext) -> PartyStateStore:
        """Get or create the party state store."""
        if self._store is not None:
            return self._store

        config_path = ctx.config_path
        if config_path is None:
            raise ConfigError("Config path not available")

        state_path = resolve_party_state_path(config_path)
        self._store = PartyStateStore(state_path)
        return self._store

    def _get_workspace_manager(self, ctx: CommandContext) -> PartyWorkspaceManager:
        """Get or create the workspace manager."""
        if self._workspace is not None:
            return self._workspace

        # Get workspace base from plugin config or use default
        workspace_base = ctx.plugin_config.get("workspace_base", DEFAULT_WORKSPACE_BASE)
        self._workspace = PartyWorkspaceManager(Path(workspace_base))
        return self._workspace

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        """Handle the /party command."""
        subcommand = ctx.args[0].lower() if ctx.args else "help"

        handlers = {
            "register": self._handle_register,
            "leave": self._handle_leave,
            "allow": self._handle_allow,
            "revoke": self._handle_revoke,
            "list": self._handle_list,
            "topics": self._handle_my_topics,
            "help": self._handle_help,
        }

        handler = handlers.get(subcommand, self._handle_help)
        try:
            return await handler(ctx)
        except ConfigError as exc:
            return CommandResult(
                text=f"Configuration error: {exc}",
                notify=True,
            )

    async def _handle_help(self, ctx: CommandContext) -> CommandResult:
        """Show help for party commands."""
        return CommandResult(
            text=(
                "<b>Party Mode Commands</b>\n\n"
                "<code>/party register &lt;name&gt;</code> - Register this topic\n"
                "<code>/party allow @user</code> - Allow a user to use your topic\n"
                "<code>/party revoke @user</code> - Revoke a user's access\n"
                "<code>/party leave</code> - Unregister the current topic\n"
                "<code>/party topics</code> - List your topics\n"
                "<code>/party list</code> - Show all party topics\n"
                "<code>/party help</code> - Show this help message\n\n"
                "<i>Create a topic in Telegram, then use /party register &lt;name&gt; "
                "inside it to set up your workspace!</i>"
            ),
            notify=True,
        )

    async def _handle_register(self, ctx: CommandContext) -> CommandResult:
        """Register the current topic.

        Must be called from inside a forum topic. The sender becomes the owner.
        """
        sender_id = ctx.message.sender_id
        if sender_id is None:
            return CommandResult(
                text="Could not identify sender. Please try again.",
                notify=True,
            )

        # Must be in a topic
        thread_id = _get_thread_id(ctx)
        if thread_id is None:
            return CommandResult(
                text="This command must be used inside a forum topic.\n\n"
                "Create a topic in Telegram first, then run "
                "<code>/party register &lt;name&gt;</code> inside it.",
                notify=True,
            )

        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

        store = self._get_store(ctx)
        workspace_mgr = self._get_workspace_manager(ctx)

        # Check if topic is already registered
        existing = await store.get_topic_by_thread(chat_id, thread_id)
        if existing is not None:
            return CommandResult(
                text=f"This topic is already registered as <b>{existing.name}</b>.",
                notify=True,
            )

        # Topic name is required
        topic_name = " ".join(ctx.args[1:]).strip() if len(ctx.args) > 1 else None
        if not topic_name:
            return CommandResult(
                text="Please provide a name for this topic.\n\n"
                "Usage: <code>/party register &lt;name&gt;</code>",
                notify=True,
            )

        # Check if name already exists
        if await store.topic_name_exists(topic_name):
            return CommandResult(
                text=f"A topic named <b>{topic_name}</b> already exists.\n"
                "Please choose a different name.",
                notify=True,
            )

        workspace_path = workspace_mgr.project_workspace_path(topic_name)

        # Check workspace path doesn't collide
        if workspace_mgr.workspace_exists(workspace_path):
            return CommandResult(
                text=f"A workspace for <b>{topic_name}</b> already exists.\n"
                "Please choose a different name.",
                notify=True,
            )

        # Create workspace
        try:
            workspace_mgr.create_workspace(workspace_path, topic_name, sender_id)
        except WorkspaceError as exc:
            return CommandResult(
                text=f"Failed to create workspace: {exc}",
                notify=True,
            )

        # Register topic
        try:
            await store.register_topic(
                chat_id=chat_id,
                thread_id=thread_id,
                owner_id=sender_id,
                name=topic_name,
                workspace_path=str(workspace_path),
            )
        except Exception as exc:
            workspace_mgr.cleanup_workspace(workspace_path, archive=False)
            return CommandResult(
                text=f"Failed to register: {exc}",
                notify=True,
            )

        # Add project to takopi config for hot-reload
        project_key: str | None = None
        config_path = ctx.config_path
        if config_path is not None:
            try:
                project_key = add_party_project(
                    config_path,
                    workspace_path,
                    topic_name,
                )
            except (FileNotFoundError, ValueError):
                # Non-fatal: topic is registered, just won't auto-bind
                project_key = get_party_project_key(topic_name)

        # Auto-bind topic to project in takopi's topic state
        if project_key is None:
            project_key = get_party_project_key(topic_name)

        if config_path is not None:
            with contextlib.suppress(Exception):
                bind_topic_to_project(
                    config_path,
                    chat_id,
                    thread_id,
                    project_key,
                    topic_title=topic_name,
                )

        return CommandResult(
            text=f"Topic <b>{topic_name}</b> registered!\nWorkspace: <code>{workspace_path}</code>",
            notify=True,
        )

    async def _handle_leave(self, ctx: CommandContext) -> CommandResult:
        """Unregister the current topic."""
        sender_id = ctx.message.sender_id
        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        thread_id = _get_thread_id(ctx)
        if thread_id is None:
            return CommandResult(
                text="This command must be used inside a party topic.",
                notify=True,
            )

        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

        store = self._get_store(ctx)
        workspace_mgr = self._get_workspace_manager(ctx)

        topic = await store.get_topic_by_thread(chat_id, thread_id)
        if topic is None:
            return CommandResult(
                text="This is not a registered party topic.",
                notify=True,
            )

        # Check if sender owns this topic
        if topic.owner_id != sender_id:
            return CommandResult(
                text="Only the topic owner can unregister this topic.",
                notify=True,
            )

        # Unregister topic
        await store.unregister_topic(thread_id)

        # Remove project from takopi config and unbind topic (best-effort)
        config_path = ctx.config_path
        if config_path is not None:
            with contextlib.suppress(Exception):
                remove_party_project(config_path, topic.name)
            with contextlib.suppress(Exception):
                unbind_topic(config_path, chat_id, thread_id)

        # Archive workspace
        workspace_path = Path(topic.workspace_path)
        try:
            archive_path = workspace_mgr.cleanup_workspace(workspace_path, archive=True)
        except WorkspaceError as exc:
            return CommandResult(
                text=f"Topic unregistered, but workspace archival failed: {exc}",
                notify=True,
            )

        text = f"Topic <b>{topic.name}</b> has been unregistered.\n\n"
        if archive_path:
            text += f"Workspace archived to: <code>{archive_path}</code>"
        else:
            text += "Workspace has been cleaned up."

        return CommandResult(text=text, notify=True)

    async def _handle_allow(self, ctx: CommandContext) -> CommandResult:
        """Allow another user to use the current topic."""
        sender_id = ctx.message.sender_id
        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        thread_id = _get_thread_id(ctx)
        if thread_id is None:
            return CommandResult(
                text="This command must be used inside a party topic.",
                notify=True,
            )

        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

        store = self._get_store(ctx)
        topic = await store.get_topic_by_thread(chat_id, thread_id)
        if topic is None:
            return CommandResult(
                text="This is not a registered party topic.",
                notify=True,
            )

        # Check if sender owns this topic
        if topic.owner_id != sender_id:
            return CommandResult(
                text="Only the topic owner can allow users.",
                notify=True,
            )

        # Extract mentioned user from entities
        raw = ctx.message.raw
        target_user_id = _extract_mentioned_user_id(raw)
        if target_user_id is None:
            return CommandResult(
                text="Please @mention the user you want to allow.\n\n"
                "Usage: <code>/party allow @username</code>",
                notify=True,
            )

        # Don't allow adding self
        if target_user_id == sender_id:
            return CommandResult(
                text="You're already the owner of this topic.",
                notify=True,
            )

        # Add user
        added = await store.allow_user(thread_id, target_user_id)
        if not added:
            return CommandResult(
                text=f"User {target_user_id} already has access to this topic.",
                notify=True,
            )

        return CommandResult(
            text=f"User {target_user_id} can now use this topic.",
            notify=True,
        )

    async def _handle_revoke(self, ctx: CommandContext) -> CommandResult:
        """Revoke another user's access to the current topic."""
        sender_id = ctx.message.sender_id
        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        thread_id = _get_thread_id(ctx)
        if thread_id is None:
            return CommandResult(
                text="This command must be used inside a party topic.",
                notify=True,
            )

        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

        store = self._get_store(ctx)
        topic = await store.get_topic_by_thread(chat_id, thread_id)
        if topic is None:
            return CommandResult(
                text="This is not a registered party topic.",
                notify=True,
            )

        # Check if sender owns this topic
        if topic.owner_id != sender_id:
            return CommandResult(
                text="Only the topic owner can revoke users.",
                notify=True,
            )

        # Extract mentioned user from entities
        raw = ctx.message.raw
        target_user_id = _extract_mentioned_user_id(raw)
        if target_user_id is None:
            return CommandResult(
                text="Please @mention the user you want to revoke.\n\n"
                "Usage: <code>/party revoke @username</code>",
                notify=True,
            )

        # Can't revoke self
        if target_user_id == sender_id:
            return CommandResult(
                text="You can't revoke your own access. "
                "Use <code>/party leave</code> to unregister the topic.",
                notify=True,
            )

        # Revoke user
        revoked = await store.revoke_user(thread_id, target_user_id)
        if not revoked:
            return CommandResult(
                text=f"User {target_user_id} doesn't have access to this topic.",
                notify=True,
            )

        return CommandResult(
            text=f"User {target_user_id}'s access has been revoked.",
            notify=True,
        )

    async def _handle_my_topics(self, ctx: CommandContext) -> CommandResult:
        """List topics owned by the current user."""
        sender_id = ctx.message.sender_id
        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        store = self._get_store(ctx)
        topics = await store.get_topics_by_owner(sender_id)

        if not topics:
            return CommandResult(
                text="You don't have any topics yet.\n"
                "Create a topic in Telegram, then use "
                "<code>/party register &lt;name&gt;</code> inside it!",
                notify=True,
            )

        lines = ["<b>Your Topics</b>\n"]
        for topic in sorted(topics, key=lambda t: t.name.lower()):
            lines.append(f"• <b>{topic.name}</b>")

        return CommandResult(text="\n".join(lines), notify=True)

    async def _handle_list(self, ctx: CommandContext) -> CommandResult:
        """List all registered party topics."""
        store = self._get_store(ctx)
        topics = await store.list_topics()

        if not topics:
            return CommandResult(
                text="No party topics registered yet.\n"
                "Create a topic in Telegram, then use "
                "<code>/party register &lt;name&gt;</code> inside it!",
                notify=True,
            )

        lines = ["<b>Party Topics</b>\n"]
        for topic in sorted(topics, key=lambda t: t.name.lower()):
            lines.append(f"• {topic.name}")

        return CommandResult(text="\n".join(lines), notify=True)


BACKEND = PartyCommand()
