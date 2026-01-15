# PR #151 Analysis: Invoke Command Pathway

**Source**: https://github.com/banteg/takopi/pull/151
**Author**: Lewis Freiberg (@l3wi)
**Status**: Open (as of 2026-01-15)

## Overview

PR #151 adds the ability for plugins to programmatically invoke built-in Takopi commands like `/topic`, `/trigger`, `/model`, and `/reasoning`. This enables plugins to orchestrate complex workflows by calling other commands instead of just responding to user messages.

## Key Changes

### 1. New `invoke_command` Method in CommandExecutor Protocol

**File**: `src/takopi/commands.py`

A new method was added to the `CommandExecutor` protocol:

```python
async def invoke_command(
    self,
    command: str,
    args: str = "",
    *,
    context: RunContext | None = None,
) -> CommandResult | None:
    """Invoke a built-in or plugin command.

    Allows plugins to call other commands like /topic, /trigger, etc.

    Args:
        command: The command to invoke (e.g., "topic", "/trigger").
        args: Arguments to pass to the command.
        context: Optional context override. If not provided, uses the
            ambient context from the original message.

    Returns the command result, or None if the command produced no result.
    Raises NotImplementedError if command invocation is not available.
    """
    ...
```

**Key Features**:
- **Command**: Can be with or without leading slash (e.g., `"topic"` or `"/topic"`)
- **Args**: String arguments passed to the command (same format as user would type)
- **Context Override**: Allows specifying a different project/branch context than the ambient one
- **Return Type**: `CommandResult | None`

### 2. CommandResult Type

**File**: `src/takopi/commands.py`

```python
@dataclass(frozen=True, slots=True)
class CommandResult:
    text: str
    notify: bool = True
    reply_to: MessageRef | None = None
```

Most built-in commands return `None` because they send replies as side effects. Unknown commands return an error `CommandResult`.

### 3. CommandDispatcher Type Alias

**File**: `src/takopi/telegram/commands/executor.py`

```python
CommandDispatcher = Callable[
    [str, str, RunContext | None], Awaitable[CommandResult | None]
]
```

A type alias for the dispatcher function that handles command invocation.

### 4. Implementation in _TelegramCommandExecutor

**File**: `src/takopi/telegram/commands/executor.py`

The executor was updated to:

1. Accept an optional `command_dispatcher` parameter in `__init__`
2. Implement `invoke_command` method:

```python
async def invoke_command(
    self,
    command: str,
    args: str = "",
    *,
    context: RunContext | None = None,
) -> CommandResult | None:
    if self._command_dispatcher is None:
        raise NotImplementedError(
            "Command invocation is not available in this context"
        )
    return await self._command_dispatcher(command, args, context)
```

**Behavior**:
- If no dispatcher is set, raises `NotImplementedError`
- Otherwise, delegates to the dispatcher function

### 5. New _invoke_builtin_command Function

**File**: `src/takopi/telegram/loop.py`

A new internal function that synchronously invokes built-in commands:

```python
async def _invoke_builtin_command(
    *,
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    command_id: str,
    args_text: str,
    ambient_context: RunContext | None,
    context_override: RunContext | None,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    resolved_scope: str | None,
    scope_chat_ids: frozenset[int],
) -> CommandResult | None:
```

**Key Points**:
- Handles command routing to specific handlers
- **Context Precedence**: `context_override` takes precedence over `ambient_context`
- Supports these commands:
  - `file` - File transfer commands
  - `ctx` - Context management
  - `new` - Create new topics
  - `topic` - Topic management
  - `agent` - Agent selection
  - `model` - Model configuration
  - `reasoning` - Reasoning mode toggle
  - `trigger` - Trigger management
- Returns `None` for valid commands (they send replies as side effects)
- Returns `CommandResult` with error message for unknown commands

### 6. make_command_dispatcher Factory

**File**: `src/takopi/telegram/loop.py`

A factory function that creates a command dispatcher closure:

```python
def make_command_dispatcher(
    msg: TelegramIncomingMessage,
    ambient_context: RunContext | None,
) -> CommandDispatcher:
    """Create a command dispatcher for plugin invocation."""

    async def dispatch(
        command: str,
        args: str = "",
        context_override: RunContext | None = None,
    ) -> CommandResult | None:
        return await _invoke_builtin_command(
            cfg=cfg,
            msg=msg,
            command_id=command.lower().lstrip("/"),
            args_text=args,
            ambient_context=ambient_context,
            context_override=context_override,
            topic_store=topic_store,
            chat_prefs=chat_prefs,
            resolved_scope=resolved_topics_scope,
            scope_chat_ids=topics_chat_ids,
        )

    return dispatch
```

**Key Features**:
- Captures loop state (config, stores, etc.) in closure
- Normalizes command ID: lowercases and strips leading `/`
- Passes through ambient context and allows override

## Usage Examples

### Example 1: Simple Command Invocation

```python
async def handle_command(ctx: CommandContext, exec: CommandExecutor) -> None:
    # Register a new topic
    result = await exec.invoke_command("topic", "myproject @main")
    # result is None (topic command sends reply as side effect)
```

### Example 2: With Context Override

```python
async def handle_command(ctx: CommandContext, exec: CommandExecutor) -> None:
    # Set up trigger in a different context
    custom_context = RunContext(project="other-project", branch="dev")
    result = await exec.invoke_command(
        "trigger",
        "mentions",
        context=custom_context
    )
```

### Example 3: Error Handling

```python
async def handle_command(ctx: CommandContext, exec: CommandExecutor) -> None:
    try:
        result = await exec.invoke_command("unknown_cmd", "args")
        if result is not None:
            # Got an error result
            print(f"Command failed: {result.text}")
    except NotImplementedError:
        # Command invocation not available in this context
        print("Cannot invoke commands here")
```

### Example 4: Party Plugin Use Case

The party plugin could use this to automatically set up topics:

```python
async def setup_workspace(ctx: CommandContext, exec: CommandExecutor) -> None:
    """Automatically register topic and configure triggers."""

    # Register the topic
    await exec.invoke_command("topic", f"{ctx.project} @{ctx.branch}")

    # Set up default triggers
    await exec.invoke_command("trigger", "mentions")

    # Configure preferred model
    await exec.invoke_command("model", "claude-opus-4")
```

## Integration with takopi-party

### Current State

The party plugin currently:
1. Responds to `/party` commands to manage workspace access
2. Uses state store to track topics and permissions
3. Cannot programmatically register topics or set up triggers

### With invoke_command

The party plugin can now:

```python
async def handle_party_create(
    ctx: CommandContext,
    exec: CommandExecutor,
    workspace_name: str,
    branch: str = "main"
) -> None:
    """Create a new party workspace with automatic setup."""

    # 1. Register the topic
    await exec.invoke_command("topic", f"{workspace_name} @{branch}")

    # 2. Store workspace in party state
    state.add_workspace(
        name=workspace_name,
        owner_id=ctx.message.sender_id,
        chat_id=ctx.message.chat_id,
        thread_id=ctx.message.thread_id,
    )

    # 3. Set up default triggers
    await exec.invoke_command("trigger", "mentions")

    # 4. Configure preferred settings
    await exec.invoke_command("reasoning", "on")
```

### Benefits for Party

1. **Automated Setup**: One command can create a workspace and configure it
2. **Consistency**: Use the same logic as manual commands
3. **Context Control**: Can set up triggers in specific project contexts
4. **Workflow Orchestration**: Chain multiple commands together

## Testing

The PR includes 8 new tests:

1. **test_invoke_command_builtin** - Basic invocation works
2. **test_invoke_command_not_available** - Raises when dispatcher not set
3. **test_invoke_builtin_command_unknown_returns_error** - Unknown commands return error
4. **test_invoke_builtin_command_trigger_uses_effective_context** - Context override works

## Context Precedence

**Important**: When both `ambient_context` and `context_override` are provided:

```python
effective_context = (
    context_override if context_override is not None else ambient_context
)
```

This allows plugins to:
- Use the ambient context by default (where the user message came from)
- Override to set up commands in different projects/branches

## Technical Details

### Command ID Normalization

```python
command_id=command.lower().lstrip("/")
```

- Converts to lowercase
- Strips leading `/`
- So `"Topic"`, `"/topic"`, and `"topic"` all work

### Built-in vs Plugin Commands

Currently only supports built-in commands:
- `file`, `ctx`, `new`, `topic`, `agent`, `model`, `reasoning`, `trigger`

Plugin commands are not yet supported (would return "unknown command" error).

### Side Effects vs Return Values

Most built-in commands:
- Send replies directly via `make_reply(cfg, msg)`
- Return `None`

Only unknown commands return a `CommandResult` with error text.

## Related Documentation

The PR also includes a comprehensive hooks specification document:
- `docs/tasks/transport-agnostic-hooks.md` - Details the planned hooks system refactor
- `docs/tasks/takopi-hooks-spec.md` - Full hooks specification with types and examples

## Migration Notes

For existing plugins using CommandExecutor:

1. **No Breaking Changes**: The `invoke_command` method is new, doesn't affect existing code
2. **Optional Feature**: Plugins can check if available with try/except
3. **Context Handling**: Be aware of ambient vs override context semantics

## Future Enhancements

Based on the PR discussion and code:

1. **Plugin Command Invocation**: Extend to support plugin-registered commands
2. **Return Value Standardization**: Make all commands optionally return results
3. **Command Chaining**: Helper for sequential command execution
4. **Transaction Semantics**: Rollback if command chain fails
5. **Hooks Integration**: Use invoke_command within hook implementations

## Summary

This PR enables powerful command orchestration for plugins:

- ✅ Plugins can invoke built-in commands programmatically
- ✅ Full control over context (use ambient or override)
- ✅ Proper error handling (NotImplementedError or CommandResult)
- ✅ Well-tested with 8 new test cases
- ✅ Non-breaking change (additive only)

**Recommendation**: This is a valuable addition for the party plugin, enabling automated workspace setup and configuration workflows.
