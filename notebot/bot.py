"""NoteDCB - a meeting notes checklist bot for Discord."""
from __future__ import annotations

import logging
import os
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

from .store import Note, NoteStore

log = logging.getLogger("notedcb")

ACCENT = discord.Colour.from_rgb(46, 125, 110)  # teal, matches the logo
PAGE_SIZE = 10
MAX_NOTE_LEN = 500

Status = app_commands.Choice[str]
STATUS_CHOICES = [
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Open", value="open"),
    app_commands.Choice(name="Done", value="done"),
]


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def progress_bar(done: int, total: int, width: int = 12) -> str:
    if total == 0:
        return "░" * width + "  0%"
    filled = round(width * done / total)
    pct = round(100 * done / total)
    return "█" * filled + "░" * (width - filled) + f"  {pct}%"


def format_note(n: Note) -> str:
    box = "☑" if n.done else "☐"
    text = f"~~{n.text}~~" if n.done else n.text
    tag = f" `{n.meeting}`" if n.meeting else ""
    return f"{box} **#{n.id}** {text}{tag}"


def notes_embed(title: str, notes: list[Note], page: int, total_done: int,
                total_all: int, footer: str = "") -> discord.Embed:
    pages = max(1, -(-len(notes) // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chunk = notes[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    embed = discord.Embed(title=title, colour=ACCENT)
    embed.description = "\n".join(format_note(n) for n in chunk) or "*No notes to show.*"
    embed.add_field(
        name="Progress",
        value=f"`{progress_bar(total_done, total_all)}`  {total_done}/{total_all} complete",
        inline=False,
    )
    embed.set_footer(text=f"Page {page + 1}/{pages}" + (f" • {footer}" if footer else ""))
    return embed


class PagedNotes(discord.ui.View):
    """Prev/next buttons for long lists. Only the invoking user can flip pages."""

    def __init__(self, owner_id: int, title: str, notes: list[Note],
                 done: int, total: int, footer: str = "") -> None:
        super().__init__(timeout=180)
        self.owner_id, self.title, self.notes = owner_id, title, notes
        self.done, self.total, self.footer = done, total, footer
        self.page = 0
        self.pages = max(1, -(-len(notes) // PAGE_SIZE))
        self._sync()

    def _sync(self) -> None:
        self.prev.disabled = self.page == 0
        self.next.disabled = self.page >= self.pages - 1

    def embed(self) -> discord.Embed:
        return notes_embed(self.title, self.notes, self.page, self.done, self.total, self.footer)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Run the command yourself to page through the list.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)


async def send_list(interaction: discord.Interaction, title: str, notes: list[Note],
                    done: int, total: int, footer: str = "") -> None:
    view = PagedNotes(interaction.user.id, title, notes, done, total, footer)
    if view.pages == 1:
        await interaction.response.send_message(embed=view.embed())
    else:
        await interaction.response.send_message(embed=view.embed(), view=view)


# --------------------------------------------------------------------------
# Bot + commands
# --------------------------------------------------------------------------
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
    group = app_commands.Group(
        name="note", description="Meeting notes and checklist", guild_only=True)

    async def id_autocomplete(interaction: discord.Interaction, current: str,
                              status: str = "all") -> list[app_commands.Choice[int]]:
        notes = store.list(interaction.guild_id, status)
        current = current.lower().lstrip("#")
        out = []
        for n in notes:
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

    async def meeting_ac(interaction: discord.Interaction, current: str):
        names = store.meetings(interaction.guild_id)
        return [app_commands.Choice(name=m[:100], value=m[:100]) for m in names
                if current.lower() in m.lower()][:25]

    @group.command(name="add", description="Add a note to the checklist")
    @app_commands.describe(text="What needs to be tracked or accomplished",
                           meeting="Optional meeting name or date, e.g. 'Weekly sync'")
    @app_commands.autocomplete(meeting=meeting_ac)
    async def add(interaction: discord.Interaction,
                  text: app_commands.Range[str, 1, MAX_NOTE_LEN],
                  meeting: Optional[app_commands.Range[str, 1, 60]] = None) -> None:
        note = store.add(interaction.guild_id, text, interaction.user.id, meeting)
        embed = discord.Embed(title="Note added", description=format_note(note), colour=ACCENT)
        embed.set_footer(text=f"Added by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    @group.command(name="list", description="Show the checklist")
    @app_commands.describe(status="Filter by completion", meeting="Only show one meeting")
    @app_commands.choices(status=STATUS_CHOICES)
    @app_commands.autocomplete(meeting=meeting_ac)
    async def list_(interaction: discord.Interaction,
                    status: Optional[Status] = None,
                    meeting: Optional[str] = None) -> None:
        st = status.value if status else "all"
        notes = store.list(interaction.guild_id, st, meeting)
        done, total = store.progress(interaction.guild_id, meeting)
        title = f"Checklist - {meeting}" if meeting else "Meeting checklist"
        await send_list(interaction, title, notes, done, total,
                        footer=f"Showing: {st}")

    async def toggle(interaction: discord.Interaction, note_id: int, done: bool) -> None:
        existing = store.get(interaction.guild_id, note_id)
        if existing is None:
            await interaction.response.send_message(
                f"No note **#{note_id}** found.", ephemeral=True)
            return
        if existing.done == done:
            state = "already checked off" if done else "already open"
            await interaction.response.send_message(
                f"Note **#{note_id}** is {state}.", ephemeral=True)
            return
        note = store.set_done(interaction.guild_id, note_id, done, interaction.user.id)
        d, t = store.progress(interaction.guild_id, note.meeting)
        embed = discord.Embed(
            title="Checked off" if done else "Re-opened",
            description=format_note(note), colour=ACCENT)
        scope = f"{note.meeting}: " if note.meeting else ""
        embed.set_footer(text=f"{scope}{d}/{t} complete")
        await interaction.response.send_message(embed=embed)

    @group.command(name="check", description="Check a note off as complete")
    @app_commands.describe(note="The note to check off")
    @app_commands.autocomplete(note=open_ac)
    async def check(interaction: discord.Interaction, note: int) -> None:
        await toggle(interaction, note, True)

    @group.command(name="uncheck", description="Mark a completed note as open again")
    @app_commands.describe(note="The note to re-open")
    @app_commands.autocomplete(note=done_ac)
    async def uncheck(interaction: discord.Interaction, note: int) -> None:
        await toggle(interaction, note, False)

    @group.command(name="remove", description="Delete a note")
    @app_commands.describe(note="The note to delete")
    @app_commands.autocomplete(note=any_ac)
    async def remove(interaction: discord.Interaction, note: int) -> None:
        removed = store.remove(interaction.guild_id, note)
        if removed is None:
            await interaction.response.send_message(
                f"No note **#{note}** found.", ephemeral=True)
            return
        embed = discord.Embed(title="Note removed", description=format_note(removed),
                              colour=discord.Colour.dark_grey())
        embed.set_footer(text=f"Removed by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    @group.command(name="search", description="Search notes by text or meeting name")
    @app_commands.describe(query="Word or phrase to look for", status="Filter by completion")
    @app_commands.choices(status=STATUS_CHOICES)
    async def search(interaction: discord.Interaction,
                     query: app_commands.Range[str, 1, 100],
                     status: Optional[Status] = None) -> None:
        st = status.value if status else "all"
        hits = store.search(interaction.guild_id, query, st)
        done, total = store.progress(interaction.guild_id)
        if not hits:
            await interaction.response.send_message(
                f"No notes matched **{discord.utils.escape_markdown(query)}**.", ephemeral=True)
            return
        await send_list(interaction, f"Search: {query}"[:256], hits, done, total,
                        footer=f"{len(hits)} match(es)")

    @group.command(name="clear-done", description="Delete all completed notes")
    @app_commands.describe(meeting="Only clear notes from this meeting")
    @app_commands.autocomplete(meeting=meeting_ac)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear_done(interaction: discord.Interaction,
                         meeting: Optional[str] = None) -> None:
        n = store.clear_done(interaction.guild_id, meeting)
        await interaction.response.send_message(
            f"Cleared **{n}** completed note(s)." if n else "No completed notes to clear.")

    @clear_done.error
    async def clear_done_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Messages** permission to clear completed notes.",
                ephemeral=True)
        else:
            raise error

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
