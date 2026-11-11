"""Button / dropdown / pop-up based interface for the checklist.

One NotesView lives on one message and moves between screens by editing that
message: Home (all folders) -> Folder (tasks) -> remove mode / confirm delete.
Everything is re-read from the database on each click, so several people can
use the same message at once.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Awaitable, Callable, Optional

import discord

from .card import Card, FolderRow, TaskRow, render_card
from .store import (
    COLOR_KEYS, ICON_KEYS, MAX_FOLDER_NAME, MAX_LINES_PER_ADD, Folder, FolderExistsError,
    FolderLimitError, Note, NoteStore, split_lines,
)

log = logging.getLogger("notedcb.ui")

ACCENT = discord.Colour.from_rgb(46, 125, 110)
WARN = discord.Colour.from_rgb(190, 70, 60)
TASK_PAGE = 8        # tasks per card: more would shrink the picture below readable size
FOLDER_PAGE = 10     # folders per card (a select menu holds at most 25)
VIEW_TIMEOUT = 840   # seconds; interaction tokens expire after 15 minutes

Callback = Callable[[discord.Interaction], Awaitable[None]]

# Optional folder styling: key -> (label, emoji for Discord menus)
ICON_CHOICES = {
    "folder": ("Folder", "📁"), "star": ("Star", "⭐"), "flag": ("Flag", "🚩"),
    "people": ("People", "👥"), "calendar": ("Calendar", "📅"), "chat": ("Chat", "💬"),
    "target": ("Target", "🎯"), "heart": ("Heart", "❤️"),
}
COLOR_CHOICES = {
    "gold": ("Gold (default)", "💛"), "teal": ("Teal", "🩵"), "blue": ("Blue", "💙"),
    "purple": ("Purple", "💜"), "pink": ("Pink", "🩷"), "red": ("Red", "❤️"),
    "orange": ("Orange", "🧡"), "green": ("Green", "💚"), "grey": ("Grey", "🩶"),
}
assert set(ICON_CHOICES) == set(ICON_KEYS) and set(COLOR_CHOICES) == set(COLOR_KEYS)


def folder_emoji(icon: Optional[str]) -> str:
    return ICON_CHOICES.get(icon or "folder", ICON_CHOICES["folder"])[1]


# ---------------------------------------------------------------- formatting
def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def progress_bar(done: int, total: int, width: int = 12) -> str:
    if total == 0:
        return "░" * width + "  0%"
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled) + f"  {round(100 * done / total)}%"


def task_line(n: Note, show_folder: bool = False) -> str:
    box = "☑" if n.done else "☐"
    text = clip(n.text, 120)
    if n.done:
        text = f"~~{text}~~"
    tag = f"  `{n.folder}`" if show_folder else ""
    return f"{box} **#{n.id}** {text}{tag}"


def search_embed(query: str, hits: list[Note]) -> discord.Embed:
    shown = hits[:25]
    embed = discord.Embed(
        title=clip(f"Search: {query}", 256),
        description="\n".join(task_line(n, show_folder=True) for n in shown),
        colour=ACCENT)
    more = f" (showing first {len(shown)})" if len(hits) > len(shown) else ""
    embed.set_footer(text=f"{len(hits)} match(es){more}")
    return embed


def can_manage(interaction: discord.Interaction) -> bool:
    perms = interaction.permissions
    return bool(perms.manage_messages or perms.administrator)


# ---------------------------------------------------------------- modals
class NewFolderModal(discord.ui.Modal, title="New folder"):
    name = discord.ui.TextInput(
        label="Folder name", placeholder="e.g. Weekly sync - Nov 10",
        max_length=MAX_FOLDER_NAME)
    tasks = discord.ui.TextInput(
        label="Tasks (optional) - one per line", style=discord.TextStyle.paragraph,
        placeholder="Review last week's action items\nConfirm budget\nPick next date",
        required=False, max_length=4000)

    def __init__(self, view: "NotesView") -> None:
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        v = self.view
        try:
            folder = v.store.get_or_create_folder(v.guild_id, self.name.value)
        except FolderLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        lines = split_lines(self.tasks.value or "")
        v.store.add_many(v.guild_id, folder.id, lines[:MAX_LINES_PER_ADD], interaction.user.id)
        v.open_folder(folder.id)
        await v.show(interaction)
        if len(lines) > MAX_LINES_PER_ADD:
            await interaction.followup.send(
                f"Only the first {MAX_LINES_PER_ADD} lines were added. Use **Add tasks** for the rest.",
                ephemeral=True)


class AddTasksModal(discord.ui.Modal, title="Add tasks"):
    tasks = discord.ui.TextInput(
        label="One task per line", style=discord.TextStyle.paragraph,
        placeholder="Press Enter for a new line - each line becomes its own task",
        max_length=4000)

    def __init__(self, view: "NotesView") -> None:
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        v = self.view
        lines = split_lines(self.tasks.value)
        if not lines or v.folder_id is None:
            await interaction.response.send_message("No tasks found in that text.", ephemeral=True)
            return
        v.store.add_many(v.guild_id, v.folder_id, lines[:MAX_LINES_PER_ADD], interaction.user.id)
        v.mode = "view"
        v.page = 10**6  # clamped to the last page so new tasks are visible
        await v.show(interaction)
        if len(lines) > MAX_LINES_PER_ADD:
            await interaction.followup.send(
                f"Only the first {MAX_LINES_PER_ADD} lines were added. Add the rest in another batch.",
                ephemeral=True)


class RenameModal(discord.ui.Modal, title="Rename folder"):
    def __init__(self, view: "NotesView", current: str) -> None:
        super().__init__()
        self.view = view
        self.name = discord.ui.TextInput(
            label="Folder name", default=current, max_length=MAX_FOLDER_NAME)
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        v = self.view
        try:
            v.store.rename_folder(v.guild_id, v.folder_id, self.name.value)
        except FolderExistsError:
            await interaction.response.send_message(
                "A folder with that name already exists.", ephemeral=True)
            return
        await v.show(interaction)


class SearchModal(discord.ui.Modal, title="Search tasks"):
    query = discord.ui.TextInput(
        label="Search text or folder name", max_length=100)

    def __init__(self, store: NoteStore, guild_id: int) -> None:
        super().__init__()
        self.store, self.guild_id = store, guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        q = self.query.value.strip()
        hits = self.store.search(self.guild_id, q) if q else []
        if not hits:
            await interaction.response.send_message(
                f"No tasks matched **{discord.utils.escape_markdown(q)}**.", ephemeral=True)
            return
        await interaction.response.send_message(embed=search_embed(q, hits), ephemeral=True)


# ---------------------------------------------------------------- main view
class NotesView(discord.ui.View):
    def __init__(self, store: NoteStore, guild_id: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.store, self.guild_id = store, guild_id
        self.folder_id: Optional[int] = None
        self.page = 0
        self.home_page = 0   # page of the folder list on the home screen
        self.mode = "view"   # view | remove | style | confirm_delete
        self.message: Optional[discord.Message] = None
        self._card: Optional[Card] = None

    # -- navigation ----------------------------------------------------
    def open_folder(self, folder_id: Optional[int]) -> None:
        self.folder_id, self.page, self.mode = folder_id, 0, "view"

    async def _compose(self) -> dict:
        """Build the message body.

        Normally the glass card is sent as a plain attachment (Discord displays
        those larger than embed images) with a one-line hint above it. If the
        image can't be rendered, fall back to a text embed.
        """
        embed = self.render()
        card = self._card
        if card is not None:
            try:
                png = await asyncio.to_thread(render_card, card)
            except Exception:
                log.exception("Card render failed; falling back to text")
            else:
                alt = clip(embed.description or card.title, 1000)
                file = discord.File(io.BytesIO(png), filename="notes.png", description=alt)
                hint = embed.footer.text
                return {"content": f"-# {hint}" if hint else None, "embed": None, "attachments": [file]}
        return {"content": None, "embed": embed, "attachments": []}

    async def show(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(**await self._compose(), view=self)

    async def start(self, interaction: discord.Interaction) -> None:
        body = await self._compose()
        kwargs = {"view": self, "allowed_mentions": discord.AllowedMentions.none()}
        if body["content"]:
            kwargs["content"] = body["content"]
        if body["embed"] is not None:
            kwargs["embed"] = body["embed"]
        if body["attachments"]:
            kwargs["files"] = body["attachments"]
        await interaction.response.send_message(**kwargs)
        self.message = await interaction.original_response()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception,
                       item: discord.ui.Item) -> None:
        log.exception("UI error", exc_info=error)
        msg = "Something went wrong. Try `/notes` again."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # -- small builders ------------------------------------------------
    def _button(self, label: str, cb: Callback, row: int,
                style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                emoji: Optional[str] = None, disabled: bool = False) -> None:
        b = discord.ui.Button(label=label, style=style, emoji=emoji, row=row, disabled=disabled)
        b.callback = cb  # type: ignore[assignment]
        self.add_item(b)

    def _select(self, placeholder: str, options: list[discord.SelectOption], row: int,
                cb: Callable[[discord.Interaction, list[str]], Awaitable[None]],
                min_values: int = 1, max_values: int = 1) -> None:
        sel = discord.ui.Select(placeholder=placeholder, options=options, row=row,
                                min_values=min_values, max_values=max_values)

        async def _cb(interaction: discord.Interaction) -> None:
            await cb(interaction, list(sel.values))

        sel.callback = _cb  # type: ignore[assignment]
        self.add_item(sel)

    # -- rendering -----------------------------------------------------
    def render(self) -> discord.Embed:
        self.clear_items()
        self._card = None
        if self.folder_id is None:
            return self._render_home()
        folder = self.store.get_folder(self.guild_id, self.folder_id)
        if folder is None:  # deleted by someone else
            self.open_folder(None)
            return self._render_home()
        return self._render_folder(folder)

    def _render_home(self) -> discord.Embed:
        folders = self.store.folders(self.guild_id)
        pages = max(1, -(-len(folders) // FOLDER_PAGE))
        self.home_page = min(max(self.home_page, 0), pages - 1)
        shown = folders[self.home_page * FOLDER_PAGE:(self.home_page + 1) * FOLDER_PAGE]
        embed = discord.Embed(title="📋 Meeting notes", colour=ACCENT)
        if folders:
            embed.description = "\n".join(
                f"{folder_emoji(f.icon)} **{clip(f.name, 40)}**  `{progress_bar(f.done, f.total, 8)}`  {f.done}/{f.total}"
                for f in shown)
            done = sum(f.done for f in folders)
            total = sum(f.total for f in folders)
            page_note = f" • Page {self.home_page + 1}/{pages}" if pages > 1 else ""
            embed.set_footer(text=f"{len(folders)} folder(s) • {done}/{total} tasks complete{page_note}")
            subtitle = f"{len(folders)} folder(s)" + (f" · page {self.home_page + 1} of {pages}" if pages > 1 else "")
            self._card = Card(
                "Meeting notes", subtitle, done, total,
                [FolderRow(f.name, f.done, f.total, f.icon, f.color) for f in shown])
            self._select(
                "Open a folder…",
                [discord.SelectOption(label=clip(f.name, 100), value=str(f.id),
                                      description=f"{f.done}/{f.total} done", emoji=folder_emoji(f.icon))
                 for f in shown],
                row=0, cb=self._on_open_folder)
        else:
            embed.description = ("No folders yet.\n\nPress **New folder** to create one - "
                                 "you can paste a whole list and each line becomes a task.")
        if self._card is None:
            self._card = Card("Meeting notes", "Paste a list and each line becomes a task", 0, 0, [],
                              empty_text="No folders yet - press New folder", show_progress=False)
        self._button("New folder", self._on_new_folder, row=1,
                     style=discord.ButtonStyle.success, emoji="➕")
        self._button("Search", self._on_search, row=1, emoji="🔎")
        if pages > 1:
            self._button("", self._on_home_prev, row=1, emoji="◀️", disabled=self.home_page == 0)
            self._button("", self._on_home_next, row=1, emoji="▶️", disabled=self.home_page >= pages - 1)
        return embed

    def _render_folder(self, folder: Folder) -> discord.Embed:
        tasks = self.store.list(self.guild_id, "all", folder.id)
        pages = max(1, -(-len(tasks) // TASK_PAGE))
        self.page = min(max(self.page, 0), pages - 1)
        chunk = tasks[self.page * TASK_PAGE:(self.page + 1) * TASK_PAGE]

        if self.mode == "confirm_delete":
            embed = discord.Embed(
                title=f"Delete “{clip(folder.name, 80)}”?", colour=WARN,
                description=f"This permanently deletes the folder and its **{folder.total}** task(s).")
            self._button("Yes, delete", self._on_confirm_delete, row=0,
                         style=discord.ButtonStyle.danger, emoji="🗑️")
            self._button("Cancel", self._on_cancel, row=0)
            return embed

        removing = self.mode == "remove"
        embed = discord.Embed(
            title=("🗑️ Remove tasks - " if removing else folder_emoji(folder.icon) + " ") + clip(folder.name, 200),
            colour=WARN if removing else ACCENT)
        embed.description = ("\n".join(task_line(n) for n in chunk) if chunk else
                             "*This folder is empty. Press **Add tasks** to fill it.*")
        embed.add_field(
            name="Progress",
            value=f"`{progress_bar(folder.done, folder.total)}`  {folder.done}/{folder.total} complete",
            inline=False)
        styling = self.mode == "style"
        hint = ("Pick tasks below to delete them" if removing
                else "Choose an icon and color for this folder" if styling
                else "Tick tasks in the menu to check them off")
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {hint}")
        self._card = Card(
            ("Remove tasks · " if removing else "") + folder.name,
            f"Page {self.page + 1} of {pages}" if pages > 1 else
            f"{folder.done} of {folder.total} tasks complete",
            folder.done, folder.total,
            [TaskRow(n.text, n.done) for n in chunk],
            empty_text="No tasks yet - press Add tasks",
            icon=folder.icon or ("folder" if folder.color or styling else None),
            color=folder.color)

        if styling:
            self._select(
                "Choose an icon…",
                [discord.SelectOption(label=lbl, value=key, emoji=emo,
                                      default=key == (folder.icon or "folder"))
                 for key, (lbl, emo) in ICON_CHOICES.items()],
                row=0, cb=self._on_icon_select)
            self._select(
                "Choose a color…",
                [discord.SelectOption(label=lbl, value=key, emoji=emo,
                                      default=key == (folder.color or "gold"))
                 for key, (lbl, emo) in COLOR_CHOICES.items()],
                row=1, cb=self._on_color_select)
            self._button("Reset to default", self._on_reset_style, row=2, emoji="↩️",
                         disabled=not (folder.icon or folder.color))
            self._button("Done", self._on_cancel, row=2,
                         style=discord.ButtonStyle.success, emoji="✅")
            return embed

        if removing:
            if chunk:
                self._select(
                    "Select tasks to DELETE…",
                    [discord.SelectOption(label=clip(f"#{n.id} {n.text}", 100), value=str(n.id))
                     for n in chunk],
                    row=0, cb=self._on_remove_select, min_values=1, max_values=len(chunk))
            self._button("Done removing", self._on_cancel, row=1,
                         style=discord.ButtonStyle.success, emoji="✅")
            return embed

        if chunk:
            self._select(
                "Tick tasks to mark them done…",
                [discord.SelectOption(label=clip(f"#{n.id} {n.text}", 100), value=str(n.id),
                                      default=n.done) for n in chunk],
                row=0, cb=self._on_check_select, min_values=0, max_values=len(chunk))
        self._button("Add tasks", self._on_add_tasks, row=1,
                     style=discord.ButtonStyle.primary, emoji="➕")
        self._button("Remove", self._on_remove_mode, row=1, emoji="🗑️", disabled=not tasks)
        self._button("Search", self._on_search, row=1, emoji="🔎")
        self._button("Clear done", self._on_clear_done, row=1, emoji="🧹",
                     disabled=folder.done == 0)
        self._button("Style", self._on_style, row=1, emoji="🎨")
        self._button("", self._on_prev, row=2, emoji="◀️", disabled=self.page == 0)
        self._button("", self._on_next, row=2, emoji="▶️", disabled=self.page >= pages - 1)
        self._button("Folders", self._on_home, row=2, emoji="⬅️")
        self._button("Rename", self._on_rename, row=2, emoji="✏️")
        self._button("Delete folder", self._on_delete, row=2,
                     style=discord.ButtonStyle.danger, emoji="🗑️")
        return embed

    # -- callbacks -----------------------------------------------------
    async def _deny(self, interaction: discord.Interaction, what: str) -> None:
        await interaction.response.send_message(
            f"You need the **Manage Messages** permission to {what}.", ephemeral=True)

    async def _on_open_folder(self, interaction: discord.Interaction, values: list[str]) -> None:
        self.open_folder(int(values[0]))
        await self.show(interaction)

    async def _on_new_folder(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NewFolderModal(self))

    async def _on_search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SearchModal(self.store, self.guild_id))

    async def _on_home_prev(self, interaction: discord.Interaction) -> None:
        self.home_page -= 1
        await self.show(interaction)

    async def _on_home_next(self, interaction: discord.Interaction) -> None:
        self.home_page += 1
        await self.show(interaction)

    async def _on_home(self, interaction: discord.Interaction) -> None:
        self.open_folder(None)
        await self.show(interaction)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.mode = "view"
        await self.show(interaction)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        self.page -= 1
        await self.show(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        await self.show(interaction)

    async def _on_add_tasks(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddTasksModal(self))

    async def _on_rename(self, interaction: discord.Interaction) -> None:
        folder = self.store.get_folder(self.guild_id, self.folder_id)
        if folder is None:
            await self.show(interaction)
            return
        await interaction.response.send_modal(RenameModal(self, folder.name))

    async def _on_check_select(self, interaction: discord.Interaction, values: list[str]) -> None:
        tasks = self.store.list(self.guild_id, "all", self.folder_id)
        chunk = tasks[self.page * TASK_PAGE:(self.page + 1) * TASK_PAGE]
        chosen = {int(v) for v in values}
        self.store.set_many_done(
            self.guild_id,
            [n.id for n in chunk if n.id in chosen],
            [n.id for n in chunk if n.id not in chosen],
            interaction.user.id)
        await self.show(interaction)

    async def _on_style(self, interaction: discord.Interaction) -> None:
        self.mode = "style"
        await self.show(interaction)

    def _apply_style(self, icon: Optional[str], color: Optional[str], keep: str) -> None:
        folder = self.store.get_folder(self.guild_id, self.folder_id)
        if folder is None:
            return
        if keep == "color":
            color = folder.color
        elif keep == "icon":
            icon = folder.icon
        self.store.set_folder_style(self.guild_id, folder.id, icon, color)

    async def _on_icon_select(self, interaction: discord.Interaction, values: list[str]) -> None:
        self._apply_style(values[0], None, keep="color")
        await self.show(interaction)

    async def _on_color_select(self, interaction: discord.Interaction, values: list[str]) -> None:
        self._apply_style(None, values[0], keep="icon")
        await self.show(interaction)

    async def _on_reset_style(self, interaction: discord.Interaction) -> None:
        self._apply_style(None, None, keep="")
        await self.show(interaction)

    async def _on_remove_mode(self, interaction: discord.Interaction) -> None:
        self.mode = "remove"
        await self.show(interaction)

    async def _on_remove_select(self, interaction: discord.Interaction, values: list[str]) -> None:
        self.store.remove_many(self.guild_id, [int(v) for v in values])
        await self.show(interaction)

    async def _on_clear_done(self, interaction: discord.Interaction) -> None:
        if not can_manage(interaction):
            await self._deny(interaction, "clear completed tasks")
            return
        self.store.clear_done(self.guild_id, self.folder_id)
        await self.show(interaction)

    async def _on_delete(self, interaction: discord.Interaction) -> None:
        if not can_manage(interaction):
            await self._deny(interaction, "delete a folder")
            return
        self.mode = "confirm_delete"
        await self.show(interaction)

    async def _on_confirm_delete(self, interaction: discord.Interaction) -> None:
        if not can_manage(interaction):
            await self._deny(interaction, "delete a folder")
            return
        if self.folder_id is not None:
            self.store.delete_folder(self.guild_id, self.folder_id)
        self.open_folder(None)
        await self.show(interaction)
