# Task: New /party <project> workflow with invoke_command

## Overview

Implement a new `/party <project>` workflow that creates a Telegram topic, configures workspace, and sets up triggers in one command using takopi's new `invoke_command` API from PR #151.

## Current vs New Behavior

### Current: /party register (manual two-step)
1. User creates a topic manually in Telegram
2. User runs `/party register <name>` inside the topic
3. Plugin creates workspace, registers topic, binds context

### New: /party <project> (automated one-step)
1. User runs `/party <project>` in ANY message (even the main chat, not a topic)
2. Plugin creates folder in workspace, runs `git init`
3. Plugin adds project to `takopi.toml` with default branch `main`
4. Plugin invokes `/topic <project> @main` to create a Telegram forum topic
5. Plugin invokes `/trigger mentions` in that topic's context

## Technical Background

### PR #151: invoke_command API

The new `CommandExecutor.invoke_command` method allows plugins to call built-in commands:

```python
async def invoke_command(
    self,
    command: str,
    args: str = "",
    *,
    context: RunContext | None = None,
) -> CommandResult | None:
```

Key points:
- `command`: Command name (e.g., `"topic"`, `"trigger"`)
- `args`: Arguments as string (e.g., `"myproject @main"`, `"mentions"`)
- `context`: Optional `RunContext` override - takes precedence over ambient context
- Returns `None` for most commands (they send replies as side effects)
- Raises `NotImplementedError` if dispatcher not available

### Built-in Commands Available

From PR #151's `_invoke_builtin_command`:
- `topic` - Create a new Telegram forum topic bound to project/branch
- `trigger` - Set trigger mode (all, mentions, clear)
- `ctx` - Set/clear context binding
- `new` - Clear sessions
- `model` - Set model preference
- `reasoning` - Toggle reasoning mode
- `file` - File transfer

### How /topic works (from topics.py)

```python
async def _handle_topic_command(...):
    # Parse args: "project @branch" or just "project" for chat_project
    context, error = _parse_project_branch_args(args_text, ...)

    # Generate topic title from context
    title = _topic_title(runtime=cfg.runtime, context=context)

    # Create Telegram forum topic
    created = await cfg.bot.create_forum_topic(msg.chat_id, title)
    thread_id = created.message_thread_id

    # Bind context in TopicStateStore
    await store.set_context(msg.chat_id, thread_id, context, topic_title=title)

    # Send confirmation in new topic
    await cfg.exec_cfg.transport.send(channel_id=msg.chat_id, message=..., options=SendOptions(thread_id=thread_id))
```

### How /trigger mentions works (from trigger.py)

```python
async def _handle_trigger_command(...):
    # Get topic key from message
    tkey = _topic_key(msg, cfg, scope_chat_ids=scope_chat_ids)

    # If in a topic, set topic-level trigger mode
    if tkey is not None:
        await topic_store.set_trigger_mode(tkey[0], tkey[1], "mentions")
        await reply(text="topic trigger mode set to `mentions`")
```

## Implementation Plan

### Step 1: Update dependencies

```toml
# pyproject.toml
dependencies = [
    "takopi>=0.21.0",  # After PR #151 merges
]
```

### Step 2: Add _handle_create in plugin.py

```python
async def _handle_create(self, ctx: CommandContext) -> CommandResult:
    """Create a new party project with topic and triggers.

    Usage: /party <project_name>

    This command:
    1. Creates a workspace folder with git init
    2. Adds project to takopi.toml
    3. Creates a Telegram topic via /topic command
    4. Sets trigger mode to mentions-only
    """
    # Validate sender
    sender_id = ctx.message.sender_id
    if sender_id is None:
        return CommandResult(text="Could not identify sender.", notify=True)

    # Validate project name
    project_name = " ".join(ctx.args).strip()
    if not project_name:
        return CommandResult(
            text="Please provide a project name.\n\n"
            "Usage: <code>/party &lt;project_name&gt;</code>",
            notify=True,
        )

    # Check for existing workspace
    workspace_mgr = self._get_workspace_manager(ctx)
    workspace_path = workspace_mgr.project_workspace_path(project_name)

    if workspace_mgr.workspace_exists(workspace_path):
        return CommandResult(
            text=f"Workspace for <b>{project_name}</b> already exists.",
            notify=True,
        )

    # Create workspace with git init
    try:
        workspace_mgr.create_workspace(workspace_path, project_name, sender_id)
    except WorkspaceError as exc:
        return CommandResult(text=f"Failed to create workspace: {exc}", notify=True)

    # Add to takopi.toml with default branch main
    config_path = ctx.config_path
    if config_path is None:
        workspace_mgr.cleanup_workspace(workspace_path, archive=False)
        return CommandResult(text="Config path not available.", notify=True)

    try:
        project_key = add_party_project(config_path, workspace_path, project_name)
    except (FileNotFoundError, ValueError) as exc:
        workspace_mgr.cleanup_workspace(workspace_path, archive=False)
        return CommandResult(text=f"Failed to add project to config: {exc}", notify=True)

    # Invoke /topic to create Telegram forum topic
    # This creates the topic and binds it to project_key @main
    try:
        await ctx.executor.invoke_command("topic", f"{project_key} @main")
    except NotImplementedError:
        # invoke_command not available in this context
        # Clean up and fallback to manual registration
        workspace_mgr.cleanup_workspace(workspace_path, archive=False)
        remove_party_project(config_path, project_name)
        return CommandResult(
            text="Command invocation not available. Please create a topic manually and use "
            "<code>/party register</code> instead.",
            notify=True,
        )

    # TODO: After /topic creates the topic, we need to:
    # 1. Find the new thread_id
    # 2. Register it in party state
    # 3. Invoke /trigger mentions in that topic's context

    # For now, partial success message
    return CommandResult(
        text=f"Created project <b>{project_name}</b>!\n"
        f"Workspace: <code>{workspace_path}</code>\n"
        f"A topic should have been created. Please check it and run <code>/trigger mentions</code> there.",
        notify=True,
    )
```

### Step 3: Update handle() to route to _handle_create

```python
async def handle(self, ctx: CommandContext) -> CommandResult | None:
    """Handle the /party command."""
    if not ctx.args:
        return await self._handle_help(ctx)

    subcommand = ctx.args[0].lower()

    # Known subcommands
    known_subcommands = {"register", "leave", "allow", "revoke", "list", "topics", "help"}

    if subcommand in known_subcommands:
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
            return CommandResult(text=f"Configuration error: {exc}", notify=True)

    # Not a known subcommand - treat entire args as project name
    # e.g., /party myproject -> create project "myproject"
    return await self._handle_create(ctx)
```

### Step 4: Handle thread_id discovery (the tricky part)

The challenge: After `/topic` creates a Telegram topic, we need the thread_id to:
1. Register in party state
2. Invoke `/trigger mentions` with that context

**Options:**

**Option A: Query TopicStateStore after /topic**
- After `invoke_command("topic", ...)`, query the topic store for the new thread
- Problem: We don't have direct access to TopicStateStore from the plugin

**Option B: Parse the response**
- The `/topic` command sends a message like "created topic `MyProject @main`"
- Problem: The response is sent as a side effect, not returned

**Option C: Use a different approach**
- Instead of invoking /topic, create the topic ourselves using `cfg.bot.create_forum_topic`
- Problem: We don't have access to `cfg.bot` from the plugin

**Option D: Two-phase with user action**
- Create workspace + config
- Tell user to run `/topic project @main` manually
- Then `/trigger mentions` in the new topic
- Less automated but more reliable

**Option E: Register state first, let binding happen**
- The `/topic` command already binds context via `TopicStateStore.set_context`
- We just need to also register in party state
- Add a listener/hook for topic creation? (Future enhancement)

**Recommended: Start with Option D** - partial automation

### Step 5: Update help text

```python
async def _handle_help(self, ctx: CommandContext) -> CommandResult:
    return CommandResult(
        text=(
            "<b>Party Mode Commands</b>\n\n"
            "<code>/party &lt;name&gt;</code> - Create a new project workspace\n"
            "<code>/party register &lt;name&gt;</code> - Register this topic (inside topic)\n"
            "<code>/party allow @user</code> - Allow a user to use your topic\n"
            "<code>/party revoke @user</code> - Revoke a user's access\n"
            "<code>/party leave</code> - Unregister the current topic\n"
            "<code>/party topics</code> - List your topics\n"
            "<code>/party list</code> - Show all party topics\n"
            "<code>/party help</code> - Show this help message\n\n"
            "<i>Tip: Use <code>/party myproject</code> to quickly set up a new project, "
            "or create a topic first and use <code>/party register</code> inside it.</i>"
        ),
        notify=True,
    )
```

## Implementation Summary (Completed)

### Solution: Read TopicState file directly

Instead of complex approaches, we simply:
1. Wait 0.5s after `/topic` creates the Telegram topic
2. Read `telegram_topics_state.json` to find the new thread_id by project key
3. Register the topic in party state
4. Invoke `/trigger mentions` with context override

### Key Changes

1. **config.py**: Added `find_thread_for_project()` to search topic state by project key
2. **plugin.py**: Added `_handle_create()` for the full workflow
3. **plugin.py**: Updated `handle()` to route unknown subcommands to create
4. **plugin.py**: Updated help text

### Error Recovery
- If workspace created but /topic fails: cleanup workspace + config
- If topic created but party registration fails: cleanup workspace + config (topic remains)
- If /trigger fails: non-fatal, topic still usable

## Test Plan

1. **Happy path**: `/party myproject` creates workspace, adds to config, creates topic
2. **invoke_command not available**: Falls back gracefully with helpful message
3. **Workspace exists**: Returns error message
4. **Project name exists**: Returns error message
5. **Config error**: Cleans up and returns error
6. **Topic creation fails**: Cleans up and returns error

## Status

- [x] Research invoke_command API (PR #151)
- [x] Understand /topic and /trigger commands
- [x] Design implementation plan
- [x] Implement `find_thread_for_project` in config.py
- [x] Implement `_handle_create` in plugin.py
- [x] Update command routing
- [x] Update help text
- [x] Lint and format
- [x] All existing tests pass (81 tests)
- [ ] Wait for PR #151 to merge to takopi
- [ ] Update takopi dependency version
- [ ] Add tests for new functionality
- [ ] Test in production
