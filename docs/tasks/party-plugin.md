# Party Plugin Implementation Plan

## Overview

The Party plugin enables multiple users to have private conversation topics with Takopi in a shared group chat. Each user gets their own dedicated topic that only responds to them (and optionally allowed guests).

## Feature Requirements

### Commands

| Command | Description |
|---------|-------------|
| `/party register [name]` | Register user, create dedicated topic with optional custom name |
| `/party allow @username` | Allow another user to interact in your topic (run inside topic) |
| `/party revoke @username` | Remove a user's access to your topic |
| `/party leave` | Unregister user, optionally archive/delete topic |
| `/party list` | Show all registered party members |

### Behavior

1. **General/Lobby Chat**: Messages in the General topic (thread_id=None or main topic) are ignored by Takopi - this becomes a lobby for discussion and `/party` commands only.

2. **Party Topics**: Each registered user gets a topic named `🎉 {name}` where `name` is either their custom name or their username/first_name.

3. **Message Filtering**: Only the topic owner (and allowed users) can trigger Takopi responses in a party topic.

4. **Workspace Isolation**: Each party topic gets its own folder with a git repo, scoped so Takopi can only read/write that folder.

## Architecture

### State Storage

New file: `telegram_party_state.json` (alongside `telegram_topics_state.json`)

```python
@dataclass(frozen=True, slots=True)
class PartyMember:
    user_id: int
    username: str | None          # @username if available
    display_name: str             # custom name or fallback
    thread_id: int                # topic thread_id
    workspace_path: str           # e.g., /root/dev/party/{user_id}/
    registered_at: str            # ISO timestamp
    allowed_users: frozenset[int] # user_ids allowed to interact

class _PartyMemberState(msgspec.Struct):
    user_id: int
    username: str | None
    display_name: str
    thread_id: int
    workspace_path: str
    registered_at: str
    allowed_users: list[int]      # stored as list, converted to frozenset

class _PartyState(msgspec.Struct):
    version: int
    chat_id: int                  # the party chat
    members: dict[str, _PartyMemberState]  # keyed by str(user_id)
```

### Components

1. **PartyStateStore** (`src/takopi/telegram/party_state.py`)
   - Similar to `TopicStateStore` but for party members
   - CRUD operations for members
   - Thread-safe with file locking

2. **Party Command Plugin** (`src/takopi/telegram/party_plugin.py`)
   - Implements `CommandBackend` protocol
   - Handles all `/party` subcommands

3. **Message Filter Hook** (modifications to `loop.py`)
   - Check if message is in a party topic
   - Verify sender is owner or allowed user
   - Skip processing if not authorized

4. **Workspace Manager** (`src/takopi/telegram/party_workspace.py`)
   - Create workspace folders
   - Initialize git repos
   - Cleanup on leave

### Integration Points

#### loop.py Modifications

```python
# In run_main_loop(), after topic_store initialization:
party_store: PartyStateStore | None = None
if cfg.party.enabled:  # new config option
    party_store = PartyStateStore(resolve_party_state_path(config_path))

# In message processing, before dispatching to engine:
if party_store is not None:
    # Check if this is a party topic
    party_member = await party_store.get_member_by_thread(chat_id, msg.thread_id)
    if party_member is not None:
        # This is a party topic - check authorization
        if msg.sender_id not in {party_member.user_id, *party_member.allowed_users}:
            # Silently ignore - not authorized
            continue
    elif msg.thread_id is None or msg.thread_id == 0:
        # General topic - only process /party commands
        if command_id != "party":
            continue  # Ignore non-party messages in lobby
```

#### Context Resolution

For party topics, override context resolution to use the party workspace:

```python
# In _merge_topic_context or similar:
if party_member is not None:
    # Override project context to party workspace
    return RunContext(
        project=f"party_{party_member.user_id}",
        branch=None,
    )
```

### Configuration

New config section in `takopi.toml`:

```toml
[transports.telegram.party]
enabled = true
workspace_base = "/root/dev/party"  # base path for party workspaces
```

### Entry Point Registration

In `pyproject.toml`:

```toml
[project.entry-points."takopi.command_backends"]
party = "takopi.telegram.party_plugin:BACKEND"
```

## Implementation Tasks

### Phase 1: Core Infrastructure

- [x] Create `party_state.py` with `PartyStateStore`
- [x] Create `party_workspace.py` for workspace management
- [ ] Add `[transports.telegram.party]` config section to settings (requires takopi changes)

### Phase 2: Command Plugin

- [x] Create `party_plugin.py` implementing `CommandBackend`
- [x] Implement `/party register [name]` handler
- [x] Implement `/party allow @username` handler
- [x] Implement `/party revoke @username` handler
- [x] Implement `/party leave` handler
- [x] Implement `/party list` handler
- [x] Implement `/party help` handler
- [x] Register entry point in `pyproject.toml`

### Phase 3: Message Filtering (requires takopi core changes)

- [ ] Modify `loop.py` to load party state
- [ ] Add party topic authorization check
- [ ] Add lobby message filtering (only `/party` commands)
- [ ] Integrate workspace context override

### Phase 4: Workspace Integration

- [x] Create workspace folder on registration
- [x] Initialize git repo in workspace
- [ ] Configure context to use party workspace path (requires takopi changes)
- [x] Cleanup workspace on leave (archive mode)

### Phase 5: Testing

- [ ] Unit tests for `PartyStateStore`
- [ ] Unit tests for workspace manager
- [ ] Integration tests for command handlers
- [ ] End-to-end test for message filtering

---

## Implementation Notes (2025-01-12)

### Completed Implementation

The `takopi-party` package has been created as a standalone plugin package with the following structure:

```
takopi-party/
├── pyproject.toml          # Package config with entry points
├── README.md               # Documentation
├── LICENSE                 # MIT License
└── src/
    └── takopi_party/
        ├── __init__.py     # Exports BACKEND, PartyStateStore, PartyWorkspaceManager
        ├── state.py        # PartyStateStore - thread-safe state persistence
        ├── workspace.py    # PartyWorkspaceManager - git repo initialization
        └── plugin.py       # PartyCommand - CommandBackend implementation
```

### Key Implementation Details

1. **State Storage** (`state.py`):
   - Uses msgspec for JSON serialization (matching takopi patterns)
   - Thread-safe with `anyio.Lock()`
   - Atomic writes with temp file + rename
   - Automatic mtime-based cache invalidation

2. **Workspace Manager** (`workspace.py`):
   - Creates isolated folders under `workspace_base/{user_id}/`
   - Initializes git repo with user-specific config
   - Creates initial README with timestamp
   - Supports archive (move to `archived/`) or delete on cleanup

3. **Command Plugin** (`plugin.py`):
   - All subcommands implemented: register, allow, revoke, leave, list, help
   - Extracts sender info from raw message (`from` field)
   - Creates forum topic via bot API
   - Handles error cases with cleanup

### Plugin Config Requirements

The plugin expects these values in `ctx.plugin_config`:
- `workspace_base`: Path string for workspace root (default: `/root/dev/party`)
- `bot`: BotClient instance for topic creation
- `raw_message`: The raw Telegram message dict for sender extraction

### Remaining Work (requires takopi core changes)

1. **Message Filtering**: The main loop in takopi needs to:
   - Load PartyStateStore alongside TopicStateStore
   - Check if messages are in party topics
   - Filter unauthorized users from triggering responses
   - Block non-party commands in General topic

2. **Context Resolution**: Need to override project context for party topics to use the workspace path

3. **Config Section**: Add `[plugins.party]` or `[transports.telegram.party]` support to pass config to plugin

## Detailed Implementation

### PartyStateStore Class

```python
class PartyStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._state = _PartyState(version=1, chat_id=0, members={})

    async def get_member(self, user_id: int) -> PartyMember | None:
        """Get party member by user_id."""
        ...

    async def get_member_by_thread(
        self, chat_id: int, thread_id: int | None
    ) -> PartyMember | None:
        """Get party member by thread_id."""
        ...

    async def register(
        self,
        user_id: int,
        username: str | None,
        display_name: str,
        thread_id: int,
        workspace_path: str,
    ) -> PartyMember:
        """Register a new party member."""
        ...

    async def unregister(self, user_id: int) -> PartyMember | None:
        """Unregister a party member, returns the member if found."""
        ...

    async def allow_user(self, owner_id: int, guest_id: int) -> bool:
        """Allow a guest user in owner's topic."""
        ...

    async def revoke_user(self, owner_id: int, guest_id: int) -> bool:
        """Revoke a guest user from owner's topic."""
        ...

    async def list_members(self) -> list[PartyMember]:
        """List all registered party members."""
        ...
```

### Party Command Handler

```python
class PartyCommand:
    id = "party"
    description = "Manage party mode - private topics for multiple users"

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
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
        return await handler(ctx)
```

### Workspace Manager

```python
class PartyWorkspaceManager:
    def __init__(self, base_path: Path) -> None:
        self._base = base_path

    def create_workspace(self, user_id: int, display_name: str) -> Path:
        """Create workspace folder and init git repo."""
        workspace = self._base / str(user_id)
        workspace.mkdir(parents=True, exist_ok=True)

        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )

        # Create initial commit
        readme = workspace / "README.md"
        readme.write_text(f"# Party Workspace: {display_name}\n")
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )

        return workspace

    def cleanup_workspace(self, user_id: int, archive: bool = True) -> None:
        """Remove or archive workspace."""
        workspace = self._base / str(user_id)
        if not workspace.exists():
            return
        if archive:
            archive_path = self._base / "archived" / f"{user_id}_{timestamp}"
            workspace.rename(archive_path)
        else:
            shutil.rmtree(workspace)
```

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| User creates topic but registration fails | Transaction-like approach: create topic last, after workspace setup |
| Workspace path conflicts | Use user_id as folder name, guaranteed unique |
| State file corruption | Atomic writes with temp file + rename (already used in topic_state) |
| Message loop performance | Lazy load party state, cache in memory with mtime check |
| Bot can't create topics | Require supergroup with forum enabled, fail fast with clear error |

## Open Questions

1. **Archive vs Delete on Leave**: Should we archive workspaces or delete them? Currently planning archive.

2. **Multiple Party Chats**: Should a user be able to have party topics in multiple chats? Current design assumes one party chat.

3. **Admin Override**: Should group admins be able to interact with all party topics?

4. **Topic Cleanup**: If a topic is manually deleted, how do we handle orphaned party state?

## Future Enhancements

- `/party transfer @newowner` - transfer topic ownership
- `/party settings` - configure topic behavior (notifications, etc.)
- Web dashboard for party management
- Automatic topic archival after inactivity
