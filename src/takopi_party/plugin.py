"""Party command plugin - manages multi-user private topics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from takopi.commands import CommandContext, CommandResult
from takopi.config import ConfigError
from takopi.telegram.client import BotClient

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
        display_name = username or str(sender_id)

    return sender_id, username, display_name


def _parse_username_arg(arg: str) -> str | None:
    """Parse a @username argument, removing the @ prefix."""
    arg = arg.strip()
    if arg.startswith("@"):
        return arg[1:]
    return arg if arg else None


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
                "`/party register [name]` - Register and get your own topic\n"
                "`/party allow @username` - Allow someone in your topic\n"
                "`/party revoke @username` - Remove access from your topic\n"
                "`/party leave` - Leave party mode and archive your workspace\n"
                "`/party list` - Show all party members\n"
                "`/party help` - Show this help message\n\n"
                "_Use `/party register` to get started!_"
            ),
            notify=True,
        )

    async def _handle_register(self, ctx: CommandContext) -> CommandResult:
        """Register a new party member and create their topic."""
        # Get sender info
        raw = ctx.plugin_config.get("raw_message")
        sender_id, username, auto_name = _extract_sender_info(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender. Please try again.",
                notify=True,
            )

        # Get custom name or use auto-generated
        display_name = " ".join(ctx.args[1:]).strip() if len(ctx.args) > 1 else None
        if not display_name:
            display_name = auto_name or f"User {sender_id}"

        store = self._get_store(ctx)
        workspace_mgr = self._get_workspace_manager(ctx)
        bot = self._get_bot(ctx)

        # Check if already registered
        existing = await store.get_member(sender_id)
        if existing is not None:
            return CommandResult(
                text=f"You're already registered as *{existing.display_name}*!",
                notify=True,
            )

        # Get chat_id from message
        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            return CommandResult(
                text="Party mode only works in Telegram group chats.",
                notify=True,
            )

        # Create workspace
        try:
            workspace_path = workspace_mgr.create_workspace(sender_id, display_name)
        except WorkspaceError as exc:
            return CommandResult(
                text=f"Failed to create workspace: {exc}",
                notify=True,
            )

        # Create topic
        topic_name = f"{display_name}"
        try:
            topic = await bot.create_forum_topic(chat_id, topic_name)
        except Exception as exc:
            # Cleanup workspace on topic creation failure
            workspace_mgr.cleanup_workspace(sender_id, archive=False)
            return CommandResult(
                text=f"Failed to create topic: {exc}\n\n"
                "Make sure this is a forum-enabled group and the bot has "
                "permission to manage topics.",
                notify=True,
            )

        if topic is None:
            workspace_mgr.cleanup_workspace(sender_id, archive=False)
            return CommandResult(
                text="Failed to create topic. Make sure this is a forum-enabled "
                "group and the bot has permission to manage topics.",
                notify=True,
            )

        thread_id = topic.message_thread_id

        # Register member
        try:
            member = await store.register(
                chat_id=chat_id,
                user_id=sender_id,
                username=username,
                display_name=display_name,
                thread_id=thread_id,
                workspace_path=str(workspace_path),
            )
        except Exception as exc:
            # Note: We can't easily delete the topic, but state will be consistent
            workspace_mgr.cleanup_workspace(sender_id, archive=False)
            return CommandResult(
                text=f"Failed to register: {exc}",
                notify=True,
            )

        return CommandResult(
            text=f"Welcome to the party, *{display_name}*!\n\n"
            f"Your topic has been created. Head over there to start chatting with Takopi.\n"
            f"Your workspace: `{member.workspace_path}`",
            notify=True,
        )

    async def _handle_allow(self, ctx: CommandContext) -> CommandResult:
        """Allow another user to interact in the owner's topic."""
        # Get sender info
        raw = ctx.plugin_config.get("raw_message")
        sender_id, _, _ = _extract_sender_info(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        store = self._get_store(ctx)

        # Check if sender is registered
        member = await store.get_member(sender_id)
        if member is None:
            return CommandResult(
                text="You're not registered. Use `/party register` first.",
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

        # Find target user by username in registered members
        members = await store.list_members()
        target_member = next(
            (m for m in members if m.username and m.username.lower() == target_username.lower()),
            None,
        )

        if target_member is None:
            return CommandResult(
                text=f"User @{target_username} is not registered in this party.\n"
                "They need to `/party register` first.",
                notify=True,
            )

        if target_member.user_id == sender_id:
            return CommandResult(
                text="You can't allow yourself - you already have access!",
                notify=True,
            )

        # Check if already allowed
        if target_member.user_id in member.allowed_users:
            return CommandResult(
                text=f"@{target_username} already has access to your topic.",
                notify=True,
            )

        # Allow the user
        success = await store.allow_user(sender_id, target_member.user_id)
        if not success:
            return CommandResult(
                text="Failed to update permissions.",
                notify=True,
            )

        return CommandResult(
            text=f"@{target_username} can now interact in your topic!",
            notify=True,
        )

    async def _handle_revoke(self, ctx: CommandContext) -> CommandResult:
        """Revoke access from a user in the owner's topic."""
        # Get sender info
        raw = ctx.plugin_config.get("raw_message")
        sender_id, _, _ = _extract_sender_info(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        store = self._get_store(ctx)

        # Check if sender is registered
        member = await store.get_member(sender_id)
        if member is None:
            return CommandResult(
                text="You're not registered. Use `/party register` first.",
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

        # Find target user by username in registered members
        members = await store.list_members()
        target_member = next(
            (m for m in members if m.username and m.username.lower() == target_username.lower()),
            None,
        )

        if target_member is None:
            return CommandResult(
                text=f"User @{target_username} is not registered in this party.",
                notify=True,
            )

        if target_member.user_id not in member.allowed_users:
            return CommandResult(
                text=f"@{target_username} doesn't have access to your topic.",
                notify=True,
            )

        # Revoke the user
        success = await store.revoke_user(sender_id, target_member.user_id)
        if not success:
            return CommandResult(
                text="Failed to update permissions.",
                notify=True,
            )

        return CommandResult(
            text=f"@{target_username}'s access to your topic has been revoked.",
            notify=True,
        )

    async def _handle_leave(self, ctx: CommandContext) -> CommandResult:
        """Leave party mode and archive workspace."""
        # Get sender info
        raw = ctx.plugin_config.get("raw_message")
        sender_id, _, _ = _extract_sender_info(raw)

        if sender_id is None:
            return CommandResult(
                text="Could not identify sender.",
                notify=True,
            )

        store = self._get_store(ctx)
        workspace_mgr = self._get_workspace_manager(ctx)

        # Check if registered
        member = await store.get_member(sender_id)
        if member is None:
            return CommandResult(
                text="You're not registered in this party.",
                notify=True,
            )

        # Unregister
        await store.unregister(sender_id)

        # Archive workspace
        try:
            archive_path = workspace_mgr.cleanup_workspace(sender_id, archive=True)
        except WorkspaceError as exc:
            # User is unregistered but workspace archive failed
            return CommandResult(
                text=f"You've left the party, but workspace archival failed: {exc}",
                notify=True,
            )

        text = f"Goodbye, *{member.display_name}*! You've left the party.\n\n"
        if archive_path:
            text += f"Your workspace has been archived to: `{archive_path}`"
        else:
            text += "Your workspace has been cleaned up."

        return CommandResult(text=text, notify=True)

    async def _handle_list(self, ctx: CommandContext) -> CommandResult:
        """List all registered party members."""
        store = self._get_store(ctx)
        members = await store.list_members()

        if not members:
            return CommandResult(
                text="No party members registered yet.\n"
                "Use `/party register` to join the party!",
                notify=True,
            )

        lines = ["*Party Members*\n"]
        for member in sorted(members, key=lambda m: m.display_name.lower()):
            username_part = f" (@{member.username})" if member.username else ""
            allowed_count = len(member.allowed_users)
            allowed_part = f" [{allowed_count} guest(s)]" if allowed_count else ""
            lines.append(f"- {member.display_name}{username_part}{allowed_part}")

        return CommandResult(text="\n".join(lines), notify=True)


BACKEND = PartyCommand()
