# takopi-party

Party mode plugin for Takopi - enables multiple users to have private conversation topics with Takopi in a shared group chat.

## Features

- **One-command setup**: Run `/party <name>` to create a project with workspace, topic, and triggers
- **Auto-binding**: Topics are automatically bound to their workspaces (no manual `/ctx set` needed)
- **Private workspaces**: Each topic gets an isolated git repository
- **Hot-reload integration**: Changes don't interrupt active sessions

## Installation

```bash
uv pip install takopi-party
```

## Requirements

- Python 3.14+
- Takopi 0.21.0+ (with invoke_command support)
- A Telegram supergroup with forum topics enabled
- Bot must have admin permissions to manage topics

## Quick Start

### 1. Create a Telegram Group

Create a new Telegram supergroup and enable forum topics:
- Create group → Convert to Supergroup → Enable Topics

### 2. Configure the Bot

Add the bot to your group with admin permissions:
- **Manage Topics** - Required to create user topics
- **Delete Messages** - Optional, for moderation

### 3. Configure Takopi

Required configuration in your `takopi.toml`:

```toml
transport = "telegram"
watch_config = true

[transports.telegram]
bot_token = "YOUR_BOT_TOKEN"
chat_id = -1001234567890  # Your supergroup chat ID
message_overflow = "split"

[transports.telegram.topics]
enabled = true

# Optional: customize workspace location (defaults to {config_dir}/party/)
# [plugins.party]
# workspace_base = "/path/to/party/workspaces"
```

### 4. Create a Project

Run `/party <name>` in any message to create a new project:

```
/party MyProject
```

The bot will:
1. Create an isolated workspace folder with a git repo
2. Add the project to `takopi.toml`
3. Create a dedicated forum topic
4. Set the topic to mentions-only trigger mode
5. Auto-bind the topic to the workspace

### 5. Start Working

Navigate to your new topic and start chatting with Takopi. Your conversations and files are isolated from other users.

## Commands

| Command | Description |
|---------|-------------|
| `/party <name>` | Create a new project with topic and workspace |
| `/party leave` | Unregister current topic and archive workspace |
| `/party list` | Show all party topics |
| `/party help` | Show help message |

## How It Works

When you run `/party <name>`, the plugin:

1. Creates a workspace directory with `git init`
2. Adds a project entry to `takopi.toml` (e.g., `[projects.party-myproject]`)
3. Invokes `/topic <project> @main` to create a Telegram forum topic
4. Invokes `/trigger mentions` to set mentions-only mode
5. Registers the topic in party state

Because takopi has `watch_config = true`, the new project is picked up automatically. The topic binding means takopi immediately knows which workspace to use.

When you run `/party leave`:

1. Unregisters the topic from party state
2. Removes the project from `takopi.toml`
3. Unbinds the topic from `telegram_topics_state.json`
4. Archives the workspace to `{workspace_base}/archived/`

## Workflow Example

```
General Topic (Lobby)
├── Alice: /party AwesomeBot
│   → Creating project AwesomeBot...
│   → Creating topic...
│   → Setting up permissions...
│   → ✓ Created project AwesomeBot!
│
├── Bob: /party MyApp
│   → ✓ Created project MyApp!
│
└── /party list
    → Party Topics:
    → • AwesomeBot
    → • MyApp

AwesomeBot Topic (mentions-only)
├── @takopi Let's build a Discord bot
├── Takopi: I'll help you set that up...
└── @takopi Can you add error handling?

MyApp Topic (mentions-only)
└── @takopi Help me with my app
```

## Configuration Reference

Optional configuration in `[plugins.party]`:

| Key | Type | Description |
|-----|------|-------------|
| `workspace_base` | `str` | Path for user workspaces (default: `{config_dir}/party/`) |

## Architecture

The plugin writes to two takopi files:

- **`takopi.toml`**: Adds `[projects.party-*]` entries for each topic
- **`telegram_topics_state.json`**: Binds forum topics to projects

This allows the plugin to work without modifying takopi core. The hot-reload feature (`watch_config = true`) ensures changes are picked up without restarting.

## License

MIT
