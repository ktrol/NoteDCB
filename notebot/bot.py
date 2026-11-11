"""NoteDCB - a meeting notes checklist bot for Discord."""
from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from .store import NoteStore
from .ui import NotesView

log = logging.getLogger("notedcb")

class NoteBot(discord.Client):
    def __init__(self, store: NoteStore) -> None:
        super().__init__(intents=discord.Intents.default())
        self.store = store
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        register_commands(self)
        dev_guild = os.getenv("DEV_GUILD_ID")
        if dev_guild:  # instant command updates while developing
            guild = discord.Object(id=int(dev_guild))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))


def register_commands(bot: NoteBot) -> None:
    store = bot.store

    # ---- the menu --------------------------------------------------------
    @bot.tree.command(name="notes", description="Open the meeting notes menu")
    @app_commands.guild_only()
    async def notes(interaction: discord.Interaction) -> None:
        await NotesView(store, interaction.guild_id).start(interaction)


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    store = NoteStore(os.getenv("DATABASE_PATH", "notes.db"))
    NoteBot(store).run(token, log_handler=None)


if __name__ == "__main__":
    main()
