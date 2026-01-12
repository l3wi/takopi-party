# takopi-party

Party mode plugin for Takopi - enables multiple users to have private conversation topics with Takopi in a shared group chat.

## Features

- Each user gets their own dedicated forum topic
- Private workspaces with git repository for each user
- Guest access control - allow specific users to interact in your topic
- Message filtering - only authorized users can trigger bot responses

## Installation

```bash
uv pip install takopi-party
```

## Requirements

- Python 3.14+
- Takopi 0.17.0+
- A Telegram group with forum topics enabled
- Bot must have permission to manage topics

## Commands

| Command | Description |
|---------|-------------|
| `/party register [name]` | Register and get your own topic with optional custom name |
| `/party allow @username` | Allow another user to interact in your topic |
| `/party revoke @username` | Remove a user's access to your topic |
| `/party leave` | Unregister and archive your workspace |
| `/party list` | Show all registered party members |
| `/party help` | Show help message |

## How It Works

1. User runs `/party register` in the General topic
2. Bot creates a new forum topic for the user
3. Bot creates an isolated workspace folder with a git repo
4. Only the topic owner (and allowed guests) can interact with Takopi in that topic
5. Messages in the General topic are ignored by Takopi (used as a lobby)

## Configuration

The plugin expects these values in `ctx.plugin_config`:

- `workspace_base`: Path string for workspace root (default: `/root/dev/party`)
- `bot`: BotClient instance for topic creation
- `raw_message`: The raw Telegram message dict for sender extraction

## Integration Notes

This plugin provides the command backend. Full party mode functionality requires integration with takopi core to:

1. Load `PartyStateStore` in the main loop
2. Filter messages from unauthorized users in party topics
3. Override context resolution to use party workspace paths

## License

MIT
