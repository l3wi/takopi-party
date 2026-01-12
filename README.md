# takopi-party

Party mode plugin for Takopi - enables multiple users to have private conversation topics with Takopi in a shared group chat.

## Features

- **Named topics**: Create dedicated forum topics for projects or conversations
- **Auto-binding**: Topics are automatically bound to their workspaces (no manual `/ctx set` needed)
- Private workspaces with git repository for each topic
- Guest access control - allow specific users to interact in your topics
- Hot-reload integration - changes don't interrupt active sessions

## Installation

```bash
uv pip install takopi-party
```

## Requirements

- Python 3.14+
- Takopi 0.17.0+
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

Enable config watching in your `takopi.toml` for seamless integration:

```toml
watch_config = true

[transports.telegram]
bot_token = "YOUR_BOT_TOKEN"
chat_id = -1001234567890  # Your supergroup chat ID

[transports.telegram.topics]
enabled = true

[plugins.party]
workspace_base = "/path/to/party/workspaces"  # Where user workspaces are created
```

### 4. Create Topics

Users create topics by running `/party register <name>` in the General topic:

```
/party register MyProject    # Creates a topic called "MyProject"
```

The bot creates:
- A dedicated forum topic
- An isolated workspace folder with a git repo
- Auto-binds the topic to takopi (ready to use immediately)

### 5. Start Working

Navigate to your topic and start chatting with Takopi. Your conversations and files are isolated from other users.

## Commands

| Command | Description |
|---------|-------------|
| `/party register <name>` | Create a new topic with the given name |
| `/party allow @user` | Allow another user to interact in current topic |
| `/party revoke @user` | Remove a user's access to current topic |
| `/party leave` | Close current topic and archive workspace |
| `/party topics` | List your own topics |
| `/party list` | Show all party topics |
| `/party help` | Show help message |

## How It Works

When you run `/party register <name>`, the plugin:

1. Creates a new forum topic in Telegram
2. Creates a workspace directory with `git init`
3. Adds a project entry to `takopi.toml` (e.g., `[projects.party-myproject]`)
4. Binds the topic to the project in `telegram_topics_state.json`

Because takopi has `watch_config = true`, the new project is picked up automatically. The topic binding means takopi immediately knows which workspace to use - no manual setup required.

When you run `/party leave`:

1. Unregisters the topic from party state
2. Removes the project from `takopi.toml`
3. Unbinds the topic from `telegram_topics_state.json`
4. Archives the workspace to `{workspace_base}/.archive/`

## Workflow Example

```
General Topic (Lobby)
├── Alice: /party register AwesomeBot
│   → Bot creates "AwesomeBot" topic + workspace at /party/awesome-bot/
│   → Auto-bound to project "party-awesomebot"
├── Alice: /party register DataPipeline
│   → Bot creates "DataPipeline" topic + workspace at /party/data-pipeline/
├── Bob: /party register MyApp
│   → Bot creates "MyApp" topic + workspace at /party/myapp/
└── (Regular chat ignored by Takopi)

AwesomeBot Topic (owned by Alice)
├── Alice: Let's build a Discord bot
├── Takopi: I'll help you set that up...
├── Alice: /party allow @bob
│   → Bob can now interact here too
└── Bob: Can you add error handling?

MyApp Topic (owned by Bob)
└── Bob: (Working on his own stuff)
```

## Access Control

Each topic has an owner (the creator) and can have additional allowed users:

- **Owner**: Full control, can allow/revoke access, can leave (archive) the topic
- **Allowed users**: Can interact with Takopi in the topic, but cannot manage access

Use `@mentions` to specify users when granting/revoking access:
```
/party allow @username   # Uses Telegram's text_mention entity to get user ID
/party revoke @username
```

## Configuration Reference

The plugin expects these values in `ctx.plugin_config`:

| Key | Type | Description |
|-----|------|-------------|
| `workspace_base` | `str` | Path for user workspaces (default: `/root/dev/party`) |
| `bot` | `BotClient` | Bot client instance for topic creation |

## Architecture

The plugin writes to two takopi files:

- **`takopi.toml`**: Adds `[projects.party-*]` entries for each topic
- **`telegram_topics_state.json`**: Binds forum topics to projects

This allows the plugin to work without modifying takopi core. The hot-reload feature (`watch_config = true`) ensures changes are picked up without restarting.

## License

MIT
