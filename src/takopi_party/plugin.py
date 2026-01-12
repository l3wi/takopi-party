"""Party command plugin - manages multi-user private topics."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from takopi.commands import CommandContext, CommandResult
from takopi.config import ConfigError
from takopi.telegram.client import BotClient

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


def _extract_sender_id(raw: dict[str, Any] | None) -> int | None:
    """Extract sender_id from raw message.

    Returns:
        The sender's user ID, or None if not available.
    """
    if raw is None:
        return None

    sender = raw.get("from")
    if not isinstance(sender, dict):
        return None

    sender_id = sender.get("id")
    if not isinstance(sender_id, int):
        return None

    return sender_id


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


def _get_thread_id_from_message(raw: dict[str, Any] | None) -> int | None:
    """Extract thread_id from raw message if in a topic."""
    if raw is None:
        return None
    thread_id = raw.get("message_thread_id")
    if isinstance(thread_id, int):
        return thread_id
    return None


def _get_thread_id(ctx: CommandContext, raw: dict[str, Any] | None) -> int | None:
    """Get thread_id from context or raw message.

    Prefers ctx.message.thread_id (if available and int), falls back to raw message.
    """
    # Try ctx.message.thread_id first (available in takopi's MessageRef)
    if hasattr(ctx.message, "thread_id") and isinstance(ctx.message.thread_id, int):
        return ctx.message.thread_id
    # Fallback to raw message extraction
    return _get_thread_id_from_message(raw)


def _get_raw_message(ctx: CommandContext) -> dict[str, Any] | None:
    """Get raw message from context.

    Prefers ctx.message.raw (if available), falls back to plugin_config.
    """
    if ctx.message.raw is not None:
        return ctx.message.raw
    return ctx.plugin_config.get("raw_message")


class PartyCommand:
    """Command to manage party mode - private topics for multiple users."""

    id = "party"
    description = "Manage party mode - private topics for multiple users"

    def __init__(self) -> None:
        self._store: PartyStateStore | None = None
        self._workspace: PartyWorkspaceManager | None = None
        self._bot: BotClient | None = None

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

    def _get_bot(self, ctx: CommandContext) -> BotClient:
        """Get the bot client from plugin config."""
        if self._bot is not None:
            return self._bot

        bot = ctx.plugin_config.get("bot")
        if bot is None:
            raise ConfigError("Bot client not available in plugin config")
        self._bot = bot
        return self._bot

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
                "*Party Mode Commands*\n\n"
                "`/party register <name>` - Create a new topic\n"
                "`/party allow @user` - Allow a user to use your topic\n"
                "`/party revoke @user` - Revoke a user's access\n"
                "`/party leave` - Close the current topic\n"
                "`/party topics` - List your topics\n"
                "`/party list` - Show all party topics\n"
                "`/party help` - Show this help message\n\n"
                "_Use `/party register <name>` in the General topic to get started!_"
            ),
            notify=True,
        )

    async def _handle_register(self, ctx: CommandContext) -> CommandResult:
        """Register a new topic."""
        raw = _get_raw_message(ctx)
        sender_id = _extract_sender_id(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender. Please try again.",
                notify=True,
            )

        store = self._get_store(ctx)
        workspace_mgr = self._get_workspace_manager(ctx)
        bot = self._get_bot(ctx)

        # Get chat_id from message
        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

        # Topic name is required
        topic_name = " ".join(ctx.args[1:]).strip() if len(ctx.args) > 1 else None
        if not topic_name:
            return CommandResult(
                text="Please provide a topic name.\n\nUsage: `/party register <name>`",
                notify=True,
            )

        # Check if name already exists
        if await store.topic_name_exists(topic_name):
            return CommandResult(
                text=f"A topic named *{topic_name}* already exists.\n"
                "Please choose a different name.",
                notify=True,
            )

        workspace_path = workspace_mgr.project_workspace_path(topic_name)

        # Check workspace path doesn't collide
        if workspace_mgr.workspace_exists(workspace_path):
            return CommandResult(
                text=f"A workspace for *{topic_name}* already exists.\n"
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

        # Create topic
        try:
            topic = await bot.create_forum_topic(chat_id, topic_name)
        except Exception as exc:
            # Cleanup workspace on topic creation failure
            workspace_mgr.cleanup_workspace(workspace_path, archive=False)
            return CommandResult(
                text=f"Failed to create topic: {exc}\n\n"
                "Make sure this is a forum-enabled group and the bot has "
                "permission to manage topics.",
                notify=True,
            )

        if topic is None:
            workspace_mgr.cleanup_workspace(workspace_path, archive=False)
            return CommandResult(
                text="Failed to create topic. Make sure this is a forum-enabled "
                "group and the bot has permission to manage topics.",
                notify=True,
            )

        thread_id = topic.message_thread_id

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
            # Note: We can't easily delete the topic, but state will be consistent
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
            text=f"Topic *{topic_name}* created and ready to use!\nWorkspace: `{workspace_path}`",
            notify=True,
        )

    async def _handle_leave(self, ctx: CommandContext) -> CommandResult:
        """Leave/close the current topic."""
        raw = _get_raw_message(ctx)
        sender_id = _extract_sender_id(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        store = self._get_store(ctx)
        workspace_mgr = self._get_workspace_manager(ctx)

        # Get current thread_id
        thread_id = _get_thread_id(ctx, raw)
        if thread_id is None:
            return CommandResult(
                text="This command must be used inside a party topic.",
                notify=True,
            )

        # Get the topic
        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

        topic = await store.get_topic_by_thread(chat_id, thread_id)
        if topic is None:
            return CommandResult(
                text="This is not a registered party topic.",
                notify=True,
            )

        # Check if sender owns this topic
        if topic.owner_id != sender_id:
            return CommandResult(
                text="Only the topic owner can close this topic.",
                notify=True,
            )

        # Unregister topic
        await store.unregister_topic(thread_id)

        # Remove project from takopi config and unbind topic (best-effort, non-fatal)
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
                text=f"Topic closed, but workspace archival failed: {exc}",
                notify=True,
            )

        text = f"Goodbye! Topic *{topic.name}* has been closed.\n\n"
        if archive_path:
            text += f"Workspace archived to: `{archive_path}`"
        else:
            text += "Workspace has been cleaned up."

        return CommandResult(text=text, notify=True)

    async def _handle_allow(self, ctx: CommandContext) -> CommandResult:
        """Allow another user to use the current topic."""
        raw = _get_raw_message(ctx)
        sender_id = _extract_sender_id(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        store = self._get_store(ctx)

        # Get current thread_id
        thread_id = _get_thread_id(ctx, raw)
        if thread_id is None:
            return CommandResult(
                text="This command must be used inside a party topic.",
                notify=True,
            )

        # Get the topic
        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

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
        target_user_id = _extract_mentioned_user_id(raw)
        if target_user_id is None:
            return CommandResult(
                text="Please @mention the user you want to allow.\n\n"
                "Usage: `/party allow @username`",
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
        raw = _get_raw_message(ctx)
        sender_id = _extract_sender_id(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        store = self._get_store(ctx)

        # Get current thread_id
        thread_id = _get_thread_id(ctx, raw)
        if thread_id is None:
            return CommandResult(
                text="This command must be used inside a party topic.",
                notify=True,
            )

        # Get the topic
        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

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
        target_user_id = _extract_mentioned_user_id(raw)
        if target_user_id is None:
            return CommandResult(
                text="Please @mention the user you want to revoke.\n\n"
                "Usage: `/party revoke @username`",
                notify=True,
            )

        # Can't revoke self
        if target_user_id == sender_id:
            return CommandResult(
                text="You can't revoke your own access. Use `/party leave` to close the topic.",
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
        raw = _get_raw_message(ctx)
        sender_id = _extract_sender_id(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        store = self._get_store(ctx)
        topics = await store.get_topics_by_owner(sender_id)

        if not topics:
            return CommandResult(
                text="You don't have any topics yet.\nUse `/party register <name>` to create one!",
                notify=True,
            )

        lines = ["*Your Topics*\n"]
        for topic in sorted(topics, key=lambda t: t.name.lower()):
            lines.append(f"- *{topic.name}*")

        return CommandResult(text="\n".join(lines), notify=True)

    async def _handle_list(self, ctx: CommandContext) -> CommandResult:
        """List all registered party topics."""
        store = self._get_store(ctx)
        topics = await store.list_topics()

        if not topics:
            return CommandResult(
                text="No party topics registered yet.\n"
                "Use `/party register <name>` to create the first one!",
                notify=True,
            )

        lines = ["*Party Topics*\n"]
        for topic in sorted(topics, key=lambda t: t.name.lower()):
            lines.append(f"- {topic.name}")

        return CommandResult(text="\n".join(lines), notify=True)


BACKEND = PartyCommand()
