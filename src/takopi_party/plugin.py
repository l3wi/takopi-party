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


def _extract_sender_info(raw: dict[str, Any] | None) -> tuple[int | None, str | None, str | None]:
    """Extract sender_id, username, and display_name from raw message.

    Returns:
        Tuple of (sender_id, username, display_name)
    """
    if raw is None:
        return None, None, None

    sender = raw.get("from")
    if not isinstance(sender, dict):
        return None, None, None

    sender_id = sender.get("id")
    if not isinstance(sender_id, int):
        return None, None, None

    username = sender.get("username")
    if not isinstance(username, str):
        username = None

    # Build display name from first_name + last_name
    first_name = sender.get("first_name", "")
    last_name = sender.get("last_name", "")
    if isinstance(first_name, str) and isinstance(last_name, str):
        display_name = f"{first_name} {last_name}".strip()
    elif isinstance(first_name, str):
        display_name = first_name
    else:
        display_name = ""

    # Fall back to username or ID if no name
    if not display_name:
        display_name = username or str(sender_id)

    return sender_id, username, display_name


def _parse_username_arg(arg: str) -> str | None:
    """Parse a @username argument, removing the @ prefix."""
    arg = arg.strip()
    if arg.startswith("@"):
        arg = arg[1:]
    return arg if arg else None


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
            "allow": self._handle_allow,
            "revoke": self._handle_revoke,
            "leave": self._handle_leave,
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
                "`/party register` - Create your personal topic\n"
                "`/party register <name>` - Create a project topic\n"
                "`/party allow @username` - Allow someone in current topic\n"
                "`/party revoke @username` - Remove access from current topic\n"
                "`/party leave` - Leave/close the current topic\n"
                "`/party topics` - List your topics\n"
                "`/party list` - Show all party topics\n"
                "`/party help` - Show this help message\n\n"
                "_Use `/party register` in the General topic to get started!_"
            ),
            notify=True,
        )

    async def _handle_register(self, ctx: CommandContext) -> CommandResult:
        """Register a new topic (personal or project)."""
        raw = _get_raw_message(ctx)
        sender_id, username, auto_name = _extract_sender_info(raw)

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

        # Determine if personal or project registration
        project_name = " ".join(ctx.args[1:]).strip() if len(ctx.args) > 1 else None
        is_personal = project_name is None

        if is_personal:
            # Personal topic - check if user already has one
            existing_personal = await store.get_personal_topic(sender_id)
            if existing_personal is not None:
                return CommandResult(
                    text=f"You already have a personal topic: *{existing_personal.name}*\n\n"
                    "Use `/party register <project-name>` to create additional project topics.",
                    notify=True,
                )

            topic_name = auto_name or f"User {sender_id}"
            workspace_path = workspace_mgr.personal_workspace_path(sender_id)
        else:
            # Project topic - check if name already exists
            if await store.topic_name_exists(project_name):
                return CommandResult(
                    text=f"A topic named *{project_name}* already exists.\n"
                    "Please choose a different name.",
                    notify=True,
                )

            topic_name = project_name
            workspace_path = workspace_mgr.project_workspace_path(project_name)

            # Check workspace path doesn't collide
            if workspace_mgr.workspace_exists(workspace_path):
                return CommandResult(
                    text=f"A workspace for *{project_name}* already exists.\n"
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
                owner_username=username,
                name=topic_name,
                workspace_path=str(workspace_path),
                is_personal=is_personal,
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
                    is_personal,
                )
            except (FileNotFoundError, ValueError):
                # Non-fatal: topic is registered, just won't auto-bind
                project_key = get_party_project_key(topic_name, is_personal)

        # Auto-bind topic to project in takopi's topic state
        if project_key is None:
            project_key = get_party_project_key(topic_name, is_personal)

        if config_path is not None:
            with contextlib.suppress(Exception):
                bind_topic_to_project(
                    config_path,
                    chat_id,
                    thread_id,
                    project_key,
                    topic_title=topic_name,
                )

        if is_personal:
            return CommandResult(
                text=f"Welcome to the party, *{topic_name}*!\n\n"
                f"Your personal topic has been created and is ready to use.\n"
                f"Workspace: `{workspace_path}`",
                notify=True,
            )
        else:
            return CommandResult(
                text=f"Project *{topic_name}* created and ready to use!\n"
                f"Workspace: `{workspace_path}`",
                notify=True,
            )

    async def _handle_allow(self, ctx: CommandContext) -> CommandResult:
        """Allow another user to interact in the current topic."""
        raw = _get_raw_message(ctx)
        sender_id, _, _ = _extract_sender_info(raw)

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

        # Parse target username
        if len(ctx.args) < 2:
            return CommandResult(
                text="Usage: `/party allow @username`",
                notify=True,
            )

        target_username = _parse_username_arg(ctx.args[1])
        if not target_username:
            return CommandResult(
                text="Please provide a valid username: `/party allow @username`",
                notify=True,
            )

        # Find target user by username in registered topics
        topics = await store.list_topics()
        target_owner_id: int | None = None
        for t in topics:
            if t.owner_username and t.owner_username.lower() == target_username.lower():
                target_owner_id = t.owner_id
                break

        if target_owner_id is None:
            return CommandResult(
                text=f"User @{target_username} is not registered in this party.\n"
                "They need to `/party register` first.",
                notify=True,
            )

        if target_owner_id == sender_id:
            return CommandResult(
                text="You can't allow yourself - you already have access!",
                notify=True,
            )

        # Check if already allowed
        if target_owner_id in topic.allowed_users:
            return CommandResult(
                text=f"@{target_username} already has access to this topic.",
                notify=True,
            )

        # Allow the user
        success = await store.allow_user(thread_id, target_owner_id)
        if not success:
            return CommandResult(
                text="Failed to update permissions.",
                notify=True,
            )

        return CommandResult(
            text=f"@{target_username} can now interact in this topic!",
            notify=True,
        )

    async def _handle_revoke(self, ctx: CommandContext) -> CommandResult:
        """Revoke access from a user in the current topic."""
        raw = _get_raw_message(ctx)
        sender_id, _, _ = _extract_sender_info(raw)

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

        # Parse target username
        if len(ctx.args) < 2:
            return CommandResult(
                text="Usage: `/party revoke @username`",
                notify=True,
            )

        target_username = _parse_username_arg(ctx.args[1])
        if not target_username:
            return CommandResult(
                text="Please provide a valid username: `/party revoke @username`",
                notify=True,
            )

        # Find target user by username in registered topics
        topics = await store.list_topics()
        target_owner_id: int | None = None
        for t in topics:
            if t.owner_username and t.owner_username.lower() == target_username.lower():
                target_owner_id = t.owner_id
                break

        if target_owner_id is None:
            return CommandResult(
                text=f"User @{target_username} is not registered in this party.",
                notify=True,
            )

        if target_owner_id not in topic.allowed_users:
            return CommandResult(
                text=f"@{target_username} doesn't have access to this topic.",
                notify=True,
            )

        # Revoke the user
        success = await store.revoke_user(thread_id, target_owner_id)
        if not success:
            return CommandResult(
                text="Failed to update permissions.",
                notify=True,
            )

        return CommandResult(
            text=f"@{target_username}'s access to this topic has been revoked.",
            notify=True,
        )

    async def _handle_leave(self, ctx: CommandContext) -> CommandResult:
        """Leave/close the current topic."""
        raw = _get_raw_message(ctx)
        sender_id, _, _ = _extract_sender_info(raw)

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
                remove_party_project(config_path, topic.name, topic.is_personal)
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

        topic_type = "personal topic" if topic.is_personal else f"project *{topic.name}*"
        text = f"Goodbye! Your {topic_type} has been closed.\n\n"
        if archive_path:
            text += f"Workspace archived to: `{archive_path}`"
        else:
            text += "Workspace has been cleaned up."

        return CommandResult(text=text, notify=True)

    async def _handle_my_topics(self, ctx: CommandContext) -> CommandResult:
        """List topics owned by the current user."""
        raw = _get_raw_message(ctx)
        sender_id, _, _ = _extract_sender_info(raw)

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
                "Use `/party register` to create your personal topic!",
                notify=True,
            )

        lines = ["*Your Topics*\n"]
        for topic in sorted(topics, key=lambda t: (not t.is_personal, t.name.lower())):
            topic_type = "📌 Personal" if topic.is_personal else "📁 Project"
            allowed_count = len(topic.allowed_users)
            allowed_part = f" [{allowed_count} guest(s)]" if allowed_count else ""
            lines.append(f"- {topic_type}: *{topic.name}*{allowed_part}")

        return CommandResult(text="\n".join(lines), notify=True)

    async def _handle_list(self, ctx: CommandContext) -> CommandResult:
        """List all registered party topics."""
        store = self._get_store(ctx)
        topics = await store.list_topics()

        if not topics:
            return CommandResult(
                text="No party topics registered yet.\n"
                "Use `/party register` to create the first one!",
                notify=True,
            )

        lines = ["*Party Topics*\n"]
        for topic in sorted(topics, key=lambda t: t.name.lower()):
            username_part = f" (@{topic.owner_username})" if topic.owner_username else ""
            topic_type = "📌" if topic.is_personal else "📁"
            allowed_count = len(topic.allowed_users)
            allowed_part = f" [{allowed_count} guest(s)]" if allowed_count else ""
            lines.append(f"- {topic_type} {topic.name}{username_part}{allowed_part}")

        return CommandResult(text="\n".join(lines), notify=True)


BACKEND = PartyCommand()
