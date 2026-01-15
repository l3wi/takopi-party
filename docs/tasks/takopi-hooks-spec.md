# Takopi Hooks Specification

## Overview

Hooks allow external code to run at specific points in takopi's lifecycle, enabling access control, logging, custom workflows, and integrations without modifying takopi core.

## Hook Types

### Pre-Session Hook (`pre_session`)

**When**: After message received, before session/agent starts

**Use Cases**:
- **Access control** - Block unauthorized users (party plugin)
- **Rate limiting** - Throttle users by message count or token usage
- **Content filtering** - Block messages containing certain patterns
- **Quota enforcement** - Check if user has remaining credits/quota
- **Maintenance mode** - Reject all sessions during maintenance windows
- **Channel restrictions** - Only allow certain engines in certain topics

**Input Context**:
```python
@dataclass
class PreSessionContext:
    sender_id: int | None
    chat_id: int
    thread_id: int | None
    message_text: str
    engine: EngineId | None
    project: str | None
    raw_message: dict[str, Any]  # Full telegram message
```

**Return**:
```python
@dataclass
class PreSessionResult:
    allow: bool
    reason: str | None = None      # Shown to user if blocked
    silent: bool = False           # If True, don't send any response
    metadata: dict[str, Any] = {}  # Passed to post_session hook
```

**Example - Party Access Control**:
```python
def party_guard(ctx: PreSessionContext) -> PreSessionResult:
    if ctx.thread_id is None:
        return PreSessionResult(allow=True)  # Not a topic, allow

    topic = state.get_topic_by_thread(ctx.chat_id, ctx.thread_id)
    if topic is None:
        return PreSessionResult(allow=True)  # Unregistered topic, allow

    if not topic.can_use_topic(ctx.sender_id):
        return PreSessionResult(
            allow=False,
            reason="You don't have access to this workspace.",
        )

    return PreSessionResult(allow=True)
```

---

### Post-Session Hook (`post_session`)

**When**: After session completes (success or failure)

**Use Cases**:
- **Usage logging** - Record tokens used, duration, user, project
- **Billing/metering** - Update user's token balance
- **Analytics** - Track which engines/projects are used most
- **Alerting** - Notify on errors or unusual patterns
- **Cleanup** - Archive temporary files, close connections
- **Audit trail** - Log all interactions for compliance

**Input Context**:
```python
@dataclass
class PostSessionContext:
    sender_id: int | None
    chat_id: int
    thread_id: int | None
    engine: EngineId
    project: str | None
    duration_ms: int
    tokens_in: int
    tokens_out: int
    status: Literal["success", "error", "cancelled"]
    error: str | None
    pre_session_metadata: dict[str, Any]  # From pre_session hook
```

**Return**:
```python
@dataclass
class PostSessionResult:
    # Post-session hooks are fire-and-forget, no return needed
    pass
```

**Example - Usage Logging**:
```python
def log_usage(ctx: PostSessionContext) -> PostSessionResult:
    db.insert("usage_log", {
        "user_id": ctx.sender_id,
        "engine": ctx.engine,
        "project": ctx.project,
        "tokens": ctx.tokens_in + ctx.tokens_out,
        "duration_ms": ctx.duration_ms,
        "status": ctx.status,
        "timestamp": datetime.now(),
    })
    return PostSessionResult()
```

---

### Pre-Message Hook (`pre_message`)

**When**: Before each message is sent to the LLM (within a session)

**Use Cases**:
- **Content injection** - Add system context, user preferences
- **PII redaction** - Strip sensitive data before sending to LLM
- **Message transformation** - Translate, reformat, expand abbreviations
- **Context augmentation** - Inject RAG results, memory, tool outputs

**Input Context**:
```python
@dataclass
class PreMessageContext:
    sender_id: int | None
    chat_id: int
    thread_id: int | None
    engine: EngineId
    project: str | None
    message_text: str
    conversation_history: list[Message]
```

**Return**:
```python
@dataclass
class PreMessageResult:
    message_text: str  # Potentially modified
    system_suffix: str | None = None  # Appended to system prompt
    skip: bool = False  # If True, don't send this message
```

---

### Post-Message Hook (`post_message`)

**When**: After LLM responds (each turn in a multi-turn session)

**Use Cases**:
- **Response filtering** - Redact sensitive info from responses
- **Response logging** - Store responses for analysis
- **Response transformation** - Format, translate, summarize
- **Side effects** - Trigger webhooks, notifications based on content
- **Safety checks** - Scan response for policy violations

**Input Context**:
```python
@dataclass
class PostMessageContext:
    sender_id: int | None
    chat_id: int
    thread_id: int | None
    engine: EngineId
    project: str | None
    user_message: str
    assistant_response: str
    tokens_in: int
    tokens_out: int
```

**Return**:
```python
@dataclass
class PostMessageResult:
    response_text: str  # Potentially modified before sending to user
    suppress: bool = False  # If True, don't send response to user
```

---

### On-Error Hook (`on_error`)

**When**: When an error occurs during session

**Use Cases**:
- **Error reporting** - Send to Sentry, PagerDuty, Slack
- **Graceful degradation** - Provide fallback response
- **Retry logic** - Determine if error is retryable
- **User notification** - Custom error messages per error type

**Input Context**:
```python
@dataclass
class OnErrorContext:
    sender_id: int | None
    chat_id: int
    thread_id: int | None
    engine: EngineId
    project: str | None
    error_type: str
    error_message: str
    traceback: str | None
    retryable: bool
```

**Return**:
```python
@dataclass
class OnErrorResult:
    custom_message: str | None = None  # Override default error message
    should_retry: bool = False
    suppress_notification: bool = False
```

---

### On-Tool-Call Hook (`on_tool_call`)

**When**: Before/after agent executes a tool

**Use Cases**:
- **Tool auditing** - Log all tool invocations
- **Tool blocking** - Prevent certain tools in certain contexts
- **Tool transformation** - Modify tool arguments
- **Sandboxing** - Run tools in isolated environments
- **Cost control** - Block expensive tool calls

**Input Context**:
```python
@dataclass
class OnToolCallContext:
    sender_id: int | None
    chat_id: int
    thread_id: int | None
    engine: EngineId
    project: str | None
    tool_name: str
    tool_args: dict[str, Any]
    phase: Literal["pre", "post"]
    result: Any | None  # Only for post phase
```

**Return**:
```python
@dataclass
class OnToolCallResult:
    allow: bool = True
    modified_args: dict[str, Any] | None = None  # For pre phase
    reason: str | None = None  # If blocked
```

---

## Configuration

### TOML Configuration

```toml
[hooks]
# Hook can be a plugin entrypoint
pre_session = "party:guard"

# Or a shell command (receives JSON on stdin, outputs JSON on stdout)
post_session = "python /scripts/log_usage.py"

# Or multiple hooks (run in order, first rejection wins for pre_*)
pre_session = ["party:guard", "rate-limiter:check"]

# Hook-specific configuration
[hooks.config.party]
state_path = "/var/lib/takopi/party.json"

[hooks.config.rate-limiter]
max_requests_per_minute = 10
```

### Plugin Entrypoint Registration

```toml
# In plugin's pyproject.toml
[project.entry-points."takopi.hooks"]
guard = "takopi_party.hooks:party_guard"
```

---

## Execution Model

### Sync vs Async

- **Pre-hooks**: Run synchronously, block until complete (needed for access control)
- **Post-hooks**: Run asynchronously, fire-and-forget (don't delay user response)

### Timeout

```toml
[hooks]
pre_session_timeout_ms = 1000   # Default 1s
post_session_timeout_ms = 5000  # Default 5s
```

### Error Handling

- **Pre-hook error**: Log error, **allow** session (fail-open for availability)
- **Post-hook error**: Log error, continue (non-blocking)
- **Configurable fail-closed mode**:
  ```toml
  [hooks]
  fail_closed = true  # Deny on pre-hook error
  ```

### Hook Ordering

When multiple hooks are configured:
```toml
pre_session = ["auth:check", "rate-limit:check", "party:guard"]
```

1. Hooks run in order
2. First rejection stops the chain
3. All must pass for session to start

---

## Implementation Phases

### Phase 1: Core Infrastructure
- Hook registration system
- Pre/post session hooks
- Plugin entrypoint loading
- Shell command execution

### Phase 2: Message Hooks
- Pre/post message hooks
- Response transformation

### Phase 3: Advanced Hooks
- On-error hooks
- On-tool-call hooks
- Hook configuration system

---

## Example: Complete Party Integration

```toml
# takopi.toml
[hooks]
pre_session = "party:guard"
post_session = "party:log"

[hooks.config.party]
state_path = "/var/lib/takopi/party/state.json"
```

```python
# takopi_party/hooks.py
from takopi.hooks import PreSessionContext, PreSessionResult, PostSessionContext
from .state import PartyStateStore

_store: PartyStateStore | None = None

def _get_store(config: dict) -> PartyStateStore:
    global _store
    if _store is None:
        _store = PartyStateStore(config.get("state_path", "party.json"))
    return _store

def party_guard(ctx: PreSessionContext, config: dict) -> PreSessionResult:
    """Block unauthorized users from registered topics."""
    if ctx.thread_id is None:
        return PreSessionResult(allow=True)

    store = _get_store(config)
    topic = store.get_topic_by_thread(ctx.chat_id, ctx.thread_id)

    if topic is None:
        return PreSessionResult(allow=True)

    if ctx.sender_id is None:
        return PreSessionResult(
            allow=False,
            reason="Could not identify sender.",
        )

    if not topic.can_use_topic(ctx.sender_id):
        return PreSessionResult(
            allow=False,
            reason=f"You don't have access to <b>{topic.name}</b>. Ask the owner to run <code>/party allow @you</code>.",
        )

    return PreSessionResult(allow=True, metadata={"topic": topic.name})

def party_log(ctx: PostSessionContext, config: dict) -> None:
    """Log usage per topic."""
    topic_name = ctx.pre_session_metadata.get("topic")
    if topic_name:
        print(f"[party] {topic_name}: {ctx.tokens_in + ctx.tokens_out} tokens")
```

---

## Open Questions

1. **Hook discovery**: Should hooks auto-register via entrypoints, or require explicit configuration?
2. **Hook isolation**: Should each hook run in a subprocess for safety?
3. **Hook dependencies**: Should hooks be able to declare dependencies on other hooks?
4. **Hot reload**: Should hooks reload when configuration changes?
5. **Hook metrics**: Should takopi expose hook execution times, success rates?
