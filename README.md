<p align="center"><img src="assets/banner.png" alt="NoteDCB" width="100%"></p>

# NoteDCB

A Discord bot that keeps a shared meeting checklist. Add notes during a meeting, check them off as they get done, and search back through them later, so everyone stays on task.

Notes are stored per server in a local SQLite file. Each note gets a short ID (`#1`, `#2`, ...) and can optionally be tagged with a meeting name.

## Commands

| Command | What it does |
|---|---|
| `/note add text [meeting]` | Add a note, optionally tagged with a meeting |
| `/note list [status] [meeting]` | Show the checklist with a progress bar (All / Open / Done) |
| `/note check note` | Check a note off |
| `/note uncheck note` | Re-open a completed note |
| `/note remove note` | Delete a note |
| `/note search query [status]` | Search note text and meeting names |
| `/note clear-done [meeting]` | Delete completed notes (requires Manage Messages) |

The `note` and `meeting` fields autocomplete from existing notes.

## Setup

1. Create an application at the [Discord Developer Portal](https://discord.com/developers/applications), add a Bot, and copy its token. Upload `assets/avatar.png` and `assets/banner.png` as the bot's icon and banner.
2. Invite it with the `bot` and `applications.commands` scopes (permissions: Send Messages, Embed Links).
3. Install and run (Python 3.10+):

```bash
pip install -r requirements.txt
cp .env.example .env     # then put your token in .env
python -m notebot
```

Set `DEV_GUILD_ID` in `.env` to have commands appear instantly in one test server; leave it blank for global commands (which can take a while to propagate the first time).

## Development

```bash
pip install -r requirements-dev.txt
pytest
python scripts/make_assets.py   # regenerate avatar and banner
```

## License

MIT
