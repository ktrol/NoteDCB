"""NoteDCB - a meeting notes checklist bot for Discord."""
from __future__ import annotations

import logging
import os
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

from .store import MAX_FOLDER_NAME, MAX_TASK_LEN, FolderLimitError, NoteStore
from .ui import ACCENT, NotesView, progress_bar, search_embed, task_line

log = logging.getLogger("notedcb")

STATUS_CHOICES = [
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Open", value="open"),
    app_commands.Choice(name="Done", value="done"),
]


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

    # ---- typed shortcuts -------------------------------------------------
    group = app_commands.Group(
        name="task", description="Quick typed shortcuts (the /notes menu does it all)",
        guild_only=True)

    async def id_autocomplete(interaction: discord.Interaction, current: str,
                              status: str) -> list[app_commands.Choice[int]]:
        current = current.lower().lstrip("#")
        out = []
        for n in store.list(interaction.guild_id, status):
            if current in str(n.id) or current in n.text.lower():
                out.append(app_commands.Choice(name=f"#{n.id} {n.text}"[:100], value=n.id))
            if len(out) == 25:
                break
        return out

    async def open_ac(i: discord.Interaction, current: str):
        return await id_autocomplete(i, current, "open")

    async def done_ac(i: discord.Interaction, current: str):
        return await id_autocomplete(i, current, "done")

    async def any_ac(i: discord.Interaction, current: str):
        return await id_autocomplete(i, current, "all")

    async def folder_ac(interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=f.name[:100], value=f.name[:100])
                for f in store.folders(interaction.guild_id)
                if current.lower() in f.name.lower()][:25]

    @group.command(name="add", description="Add one task to a folder")
    @app_commands.describe(text="What needs to be done",
                           folder=f"Folder name (created if new; default General)")
    @app_commands.autocomplete(folder=folder_ac)
    async def add(interaction: discord.Interaction,
                  text: app_commands.Range[str, 1, MAX_TASK_LEN],
                  folder: Optional[app_commands.Range[str, 1, MAX_FOLDER_NAME]] = None) -> None:
        try:
            note = store.add(interaction.guild_id, text, interaction.user.id, folder)
        except FolderLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        embed = discord.Embed(title="Task added", description=task_line(note, True), colour=ACCENT)
        embed.set_footer(text=f"Added by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    async def toggle(interaction: discord.Interaction, note_id: int, done: bool) -> None:
        existing = store.get(interaction.guild_id, note_id)
        if existing is None:
            await interaction.response.send_message(f"No task **#{note_id}** found.", ephemeral=True)
            return
        if existing.done == done:
            state = "already checked off" if done else "already open"
            await interaction.response.send_message(f"Task **#{note_id}** is {state}.", ephemeral=True)
            return
        note = store.set_done(interaction.guild_id, note_id, done, interaction.user.id)
        d, t = store.progress(interaction.guild_id, note.folder_id)
        embed = discord.Embed(title="Checked off" if done else "Re-opened",
                              description=task_line(note, True), colour=ACCENT)
        embed.set_footer(text=f"{note.folder}: {progress_bar(d, t, 8)} {d}/{t}")
        await interaction.response.send_message(embed=embed)

    @group.command(name="check", description="Check a task off as complete")
    @app_commands.describe(task="The task to check off")
    @app_commands.autocomplete(task=open_ac)
    async def check(interaction: discord.Interaction, task: int) -> None:
        await toggle(interaction, task, True)

    @group.command(name="uncheck", description="Mark a completed task as open again")
    @app_commands.describe(task="The task to re-open")
    @app_commands.autocomplete(task=done_ac)
    async def uncheck(interaction: discord.Interaction, task: int) -> None:
        await toggle(interaction, task, False)

    @group.command(name="remove", description="Delete a task")
    @app_commands.describe(task="The task to delete")
    @app_commands.autocomplete(task=any_ac)
    async def remove(interaction: discord.Interaction, task: int) -> None:
        removed = store.remove(interaction.guild_id, task)
        if removed is None:
            await interaction.response.send_message(f"No task **#{task}** found.", ephemeral=True)
            return
        embed = discord.Embed(title="Task removed", description=task_line(removed, True),
                              colour=discord.Colour.dark_grey())
        embed.set_footer(text=f"Removed by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    @group.command(name="search", description="Search tasks by text or folder name")
    @app_commands.describe(query="Word or phrase to look for", status="Filter by completion")
    @app_commands.choices(status=STATUS_CHOICES)
    async def search(interaction: discord.Interaction,
                     query: app_commands.Range[str, 1, 100],
                     status: Optional[app_commands.Choice[str]] = None) -> None:
        hits = store.search(interaction.guild_id, query, status.value if status else "all")
        if not hits:
            await interaction.response.send_message(
                f"No tasks matched **{discord.utils.escape_markdown(query)}**.", ephemeral=True)
            return
        await interaction.response.send_message(embed=search_embed(query, hits))

    bot.tree.add_command(group)


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
