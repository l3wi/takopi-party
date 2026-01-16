"""Party command plugin - manages multi-user private topics."""

from __future__ import annotations

import contextlib
from pathlib import Path

from takopi.commands import CommandContext, CommandResult
from takopi.config import ConfigError
from takopi.transport import RenderedMessage

from .config import (
    add_party_project,
    find_thread_for_project,
    remove_party_project,
    set_topic_trigger_mode,
    unbind_topic,
)
from .state import PartyStateStore, resolve_party_state_path
from .workspace import PartyWorkspaceManager, WorkspaceError


def _html(text: str) -> RenderedMessage:
    """Wrap text in a RenderedMessage with HTML parse mode."""
    return RenderedMessage(text=text, extra={"parse_mode": "HTML"})


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

        # Get workspace base from plugin config or default to {config_dir}/party/
        workspace_base = ctx.plugin_config.get("workspace_base")
        if workspace_base:
            base_path = Path(workspace_base)
        else:
            # Default: create party/ folder in same directory as takopi.toml
            config_path = ctx.config_path
            if config_path is None:
                raise ConfigError("Config path not available")
            base_path = config_path.parent / "party"

        # Ensure workspace base directory exists
        base_path.mkdir(parents=True, exist_ok=True)

        self._workspace = PartyWorkspaceManager(base_path)
        return self._workspace

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        """Handle the /party command."""
        if not ctx.args:
            await self._handle_help(ctx)
            return None

        subcommand = ctx.args[0].lower()

        # Known subcommands
        handlers = {
            "leave": self._handle_leave,
            "list": self._handle_list,
            "help": self._handle_help,
        }

        if subcommand in handlers:
            try:
                await handlers[subcommand](ctx)
            except ConfigError as exc:
                await ctx.executor.send(f"Configuration error: {exc}", reply_to=ctx.message)
            return None

        # Not a known subcommand - treat entire args as project name
        # e.g., /party myproject -> create project "myproject"
        project_name = " ".join(ctx.args).strip()
        try:
            await self._handle_create(ctx, project_name)
        except ConfigError as exc:
            await ctx.executor.send(f"Configuration error: {exc}", reply_to=ctx.message)
        return None

    async def _handle_help(self, ctx: CommandContext) -> None:
        """Show help for party commands."""
        await ctx.executor.send(
            _html(
                "<b>Party Mode Commands</b>\n\n"
                "<code>/party &lt;name&gt;</code> - Create a new project with topic\n"
                "<code>/party leave</code> - Unregister the current topic\n"
                "<code>/party list</code> - Show all party topics\n"
                "<code>/party help</code> - Show this help message"
            ),
            reply_to=ctx.message,
        )

    async def _handle_create(self, ctx: CommandContext, project_name: str) -> None:
        """Create a new party project with topic and triggers.

        This command:
        1. Creates a workspace folder with git init
        2. Adds project to takopi.toml
        3. Creates a Telegram topic via /topic command
        4. Sets trigger mode to mentions-only
        """
        import asyncio

        sender_id = ctx.message.sender_id
        if sender_id is None:
            await ctx.executor.send("Could not identify sender.", reply_to=ctx.message)
            return

        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            await ctx.executor.send(
                "Party mode only works in Telegram group chats.",
                reply_to=ctx.message,
            )
            return

        config_path = ctx.config_path
        if config_path is None:
            await ctx.executor.send("Config path not available.", reply_to=ctx.message)
            return

        store = self._get_store(ctx)
        workspace_mgr = self._get_workspace_manager(ctx)

        # Check if name already exists in party state
        if await store.topic_name_exists(project_name):
            await ctx.executor.send(
                _html(f"A topic named <b>{project_name}</b> already exists."),
                reply_to=ctx.message,
            )
            return

        workspace_path = workspace_mgr.project_workspace_path(project_name)

        # Check workspace path doesn't collide
        if workspace_mgr.workspace_exists(workspace_path):
            await ctx.executor.send(
                _html(f"A workspace for <b>{project_name}</b> already exists."),
                reply_to=ctx.message,
            )
            return

        # Send initial progress message
        await ctx.executor.send(_html(f"Creating project <b>{project_name}</b>..."))

        # Create workspace with git init
        try:
            workspace_mgr.create_workspace(workspace_path, project_name, sender_id)
        except WorkspaceError as exc:
            await ctx.executor.send(f"Failed to create workspace: {exc}", reply_to=ctx.message)
            return

        # Add to takopi.toml
        try:
            project_key = add_party_project(config_path, workspace_path, project_name)
        except (FileNotFoundError, ValueError) as exc:
            workspace_mgr.cleanup_workspace(workspace_path, archive=False)
            await ctx.executor.send(f"Failed to add project to config: {exc}", reply_to=ctx.message)
            return

        # Wait for takopi to reload config (watch_config polls for changes)
        await asyncio.sleep(1.0)

        # Invoke /topic to create Telegram forum topic
        try:
            await ctx.executor.invoke_command("topic", f"{project_key} @main")
        except NotImplementedError:
            # invoke_command not available - clean up and suggest manual flow
            workspace_mgr.cleanup_workspace(workspace_path, archive=False)
            remove_party_project(config_path, project_name)
            await ctx.executor.send(
                _html(
                    "Command invocation not available. Please create a topic manually "
                    "and use <code>/party register</code> instead."
                ),
                reply_to=ctx.message,
            )
            return

        # Wait for topic state to be written, then find the thread_id
        await asyncio.sleep(1.5)
        thread_id = find_thread_for_project(config_path, chat_id, project_key)

        if thread_id is None:
            # Topic creation may have failed or state not yet written
            # Clean up and report partial failure
            workspace_mgr.cleanup_workspace(workspace_path, archive=False)
            remove_party_project(config_path, project_name)
            await ctx.executor.send(
                _html(
                    "Failed to create topic. Please try again or use "
                    "<code>/party register</code> inside an existing topic."
                ),
                reply_to=ctx.message,
            )
            return

        # Register topic in party state
        try:
            await store.register_topic(
                chat_id=chat_id,
                thread_id=thread_id,
                owner_id=sender_id,
                name=project_name,
                workspace_path=str(workspace_path),
            )
        except Exception as exc:
            # Topic was created but party registration failed
            # Leave topic but clean up workspace
            workspace_mgr.cleanup_workspace(workspace_path, archive=False)
            remove_party_project(config_path, project_name)
            await ctx.executor.send(
                f"Topic created but registration failed: {exc}", reply_to=ctx.message
            )
            return

        # Set trigger mode to mentions-only directly in topic state
        set_topic_trigger_mode(config_path, chat_id, thread_id, "mentions")

        await ctx.executor.send(
            _html(f"Created <b>{project_name}</b>\n<code>{workspace_path}</code>"),
            reply_to=ctx.message,
        )

    async def _handle_leave(self, ctx: CommandContext) -> None:
        """Unregister the current topic."""
        sender_id = ctx.message.sender_id
        if sender_id is None:
            await ctx.executor.send("Could not identify sender.", reply_to=ctx.message)
            return

        thread_id = _get_thread_id(ctx)
        if thread_id is None:
            await ctx.executor.send(
                "This command must be used inside a party topic.", reply_to=ctx.message
            )
            return

        chat_id = ctx.message.channel_id
        if not isinstance(chat_id, int):
            await ctx.executor.send(
                "Party mode only works in Telegram group chats.", reply_to=ctx.message
            )
            return

        store = self._get_store(ctx)
        workspace_mgr = self._get_workspace_manager(ctx)

        topic = await store.get_topic_by_thread(chat_id, thread_id)
        if topic is None:
            await ctx.executor.send("This is not a registered party topic.", reply_to=ctx.message)
            return

        # Check if sender owns this topic
        if topic.owner_id != sender_id:
            await ctx.executor.send(
                "Only the topic owner can unregister this topic.", reply_to=ctx.message
            )
            return

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
            await ctx.executor.send(
                f"Topic unregistered, but workspace archival failed: {exc}",
                reply_to=ctx.message,
            )
            return

        text = f"Topic <b>{topic.name}</b> has been unregistered.\n\n"
        if archive_path:
            text += f"Workspace archived to: <code>{archive_path}</code>"
        else:
            text += "Workspace has been cleaned up."

        await ctx.executor.send(_html(text), reply_to=ctx.message)

    async def _handle_list(self, ctx: CommandContext) -> None:
        """List all registered party topics."""
        store = self._get_store(ctx)
        topics = await store.list_topics()

        if not topics:
            await ctx.executor.send(
                _html(
                    "No party topics registered yet.\n"
                    "Use <code>/party &lt;name&gt;</code> to create one!"
                ),
                reply_to=ctx.message,
            )
            return

        lines = ["<b>Party Topics</b>\n"]
        for topic in sorted(topics, key=lambda t: t.name.lower()):
            lines.append(f"• {topic.name}")

        await ctx.executor.send(_html("\n".join(lines)), reply_to=ctx.message)


BACKEND = PartyCommand()
