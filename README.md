# takopi-party

Party mode plugin for Takopi - enables multiple users to have private conversation topics with Takopi in a shared group chat.

## Features

- **Personal topics**: Each user gets their own dedicated forum topic
- **Project topics**: Create additional named topics for specific projects
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

### 4. Register Users

Users join by running `/party register` in the General topic:

```
/party register              # Creates your personal topic (named after you)
/party register MyProject    # Creates a project topic called "MyProject"
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
| `/party register` | Create your personal topic (one per user) |
| `/party register <name>` | Create a project topic with the given name |
| `/party allow @username` | Allow another user to interact in current topic |
| `/party revoke @username` | Remove a user's access to current topic |
| `/party leave` | Close current topic and archive workspace |
| `/party topics` | List your own topics |
| `/party list` | Show all party topics |
| `/party help` | Show help message |

## How It Works

When you run `/party register`, the plugin:

1. Creates a new forum topic in Telegram
2. Creates a workspace directory with `git init`
3. Adds a project entry to `takopi.toml` (e.g., `[projects.party-user-alice]`)
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
├── Alice: /party register
│   → Bot creates "Alice" topic + workspace at /party/123456/
│   → Auto-bound to project "party-user-alice"
├── Alice: /party register AwesomeBot
│   → Bot creates "AwesomeBot" topic + workspace at /party/awesome-bot/
│   → Auto-bound to project "party-awesomebot"
├── Bob: /party register
│   → Bot creates "Bob" topic + workspace at /party/789012/
└── (Regular chat ignored by Takopi)

Alice's Personal Topic
├── Alice: Help me write a Python script
├── Takopi: Sure! Let me create that...
├── Alice: /party allow @bob
│   → Bob can now interact here too
└── Bob: Can you add error handling?

AwesomeBot Project Topic
├── Alice: Let's build a Discord bot
├── Takopi: I'll help you set that up...
└── (Only Alice has access until she allows others)

Bob's Personal Topic
└── Bob: (Working on his own stuff)
```

## Topic Types

### Personal Topics
- Created with `/party register` (no name)
- Named after the user's Telegram display name
- Workspace stored at `{workspace_base}/{user_id}/`
- One personal topic per user

### Project Topics
- Created with `/party register <project-name>`
- Named after the project
- Workspace stored at `{workspace_base}/{sanitized-name}/`
- Users can create multiple project topics

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
