"""SQLite-backed storage for meeting folders and their tasks.

Layout: a Discord server (guild) has folders; a folder holds tasks.
Tasks get short per-guild numbers (#1, #2, ...) that are never reused.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Iterable, Optional

DEFAULT_FOLDER = "General"
MAX_FOLDERS = 25          # fits in one Discord select menu
MAX_TASK_LEN = 500
MAX_FOLDER_NAME = 40
MAX_LINES_PER_ADD = 50

# Optional folder look. None means "default" (gold folder).
ICON_KEYS = ("folder", "star", "flag", "people", "calendar", "chat", "target", "heart")
COLOR_KEYS = ("gold", "teal", "blue", "purple", "pink", "red", "orange", "green", "grey")

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name     TEXT    NOT NULL COLLATE NOCASE,
    icon     TEXT,
    color    TEXT,
    UNIQUE (guild_id, name)
);
CREATE TABLE IF NOT EXISTS notes (
    guild_id   INTEGER NOT NULL,
    id         INTEGER NOT NULL,
    text       TEXT    NOT NULL,
    folder_id  INTEGER NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    done_by    INTEGER,
    done_at    INTEGER,
    PRIMARY KEY (guild_id, id)
);
CREATE TABLE IF NOT EXISTS counters (
    guild_id INTEGER PRIMARY KEY,
    next_id  INTEGER NOT NULL
);
"""

_BULLET = re.compile(r"^(?:[-*•]|\[[ xX]?\])\s+")


class FolderExistsError(Exception):
    pass


class FolderLimitError(Exception):
    pass


def split_lines(raw: str) -> list[str]:
    """Turn pasted/typed notepad text into tasks: one per non-empty line.

    Leading bullets ("- ", "* ", "• ", "[ ] ") are stripped. Long lines are
    truncated to MAX_TASK_LEN.
    """
    out = []
    for line in raw.splitlines():
        line = _BULLET.sub("", line.strip()).strip()
        if line:
            out.append(line[:MAX_TASK_LEN])
    return out


@dataclass(frozen=True)
class Folder:
    id: int
    guild_id: int
    name: str
    done: int
    total: int
    icon: Optional[str] = None
    color: Optional[str] = None


@dataclass(frozen=True)
class Note:
    guild_id: int
    id: int
    text: str
    folder_id: int
    folder: str
    done: bool
    created_by: int
    created_at: int
    done_by: Optional[int]
    done_at: Optional[int]


def _note(row: sqlite3.Row) -> Note:
    return Note(
        guild_id=row["guild_id"], id=row["id"], text=row["text"],
        folder_id=row["folder_id"], folder=row["folder"], done=bool(row["done"]),
        created_by=row["created_by"], created_at=row["created_at"],
        done_by=row["done_by"], done_at=row["done_at"],
    )


def _folder(row: sqlite3.Row) -> Folder:
    return Folder(row["id"], row["guild_id"], row["name"], row["done"], row["total"],
                  row["icon"], row["color"])


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_NOTE_SELECT = (
    "SELECT n.*, f.name AS folder FROM notes n "
    "JOIN folders f ON f.id = n.folder_id AND f.guild_id = n.guild_id"
)
_FOLDER_SELECT = (
    "SELECT f.id, f.guild_id, f.name, f.icon, f.color, COALESCE(SUM(n.done), 0) AS done, "
    "COUNT(n.id) AS total FROM folders f "
    "LEFT JOIN notes n ON n.folder_id = f.id AND n.guild_id = f.guild_id"
)


class NoteStore:
    def __init__(self, path: str = "notes.db") -> None:
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._migrate_v1_if_needed()
        self._add_style_columns_if_needed()
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _migrate_v1_if_needed(self) -> None:
        """v1 stored a free-text `meeting` column; move those into folders."""
        cols = [r[1] for r in self._db.execute("PRAGMA table_info(notes)")]
        if not cols or "folder_id" in cols:
            return
        with self._db:
            self._db.execute("ALTER TABLE notes RENAME TO notes_v1")
            self._db.executescript(SCHEMA)
            for row in self._db.execute("SELECT * FROM notes_v1 ORDER BY guild_id, id").fetchall():
                folder = self._get_or_create_folder(row["guild_id"], row["meeting"] or DEFAULT_FOLDER)
                self._db.execute(
                    "INSERT INTO notes (guild_id, id, text, folder_id, done, created_by, "
                    "created_at, done_by, done_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (row["guild_id"], row["id"], row["text"], folder.id, row["done"],
                     row["created_by"], row["created_at"], row["done_by"], row["done_at"]))
            self._db.execute("DROP TABLE notes_v1")

    def _add_style_columns_if_needed(self) -> None:
        cols = [r[1] for r in self._db.execute("PRAGMA table_info(folders)")]
        if cols and "icon" not in cols:
            with self._db:
                self._db.execute("ALTER TABLE folders ADD COLUMN icon TEXT")
                self._db.execute("ALTER TABLE folders ADD COLUMN color TEXT")

    # -- folders --------------------------------------------------------
    def _clean_name(self, name: str) -> str:
        name = " ".join(name.split())[:MAX_FOLDER_NAME]
        return name or DEFAULT_FOLDER

    def _get_or_create_folder(self, guild_id: int, name: str) -> Folder:
        name = self._clean_name(name)
        row = self._db.execute(
            _FOLDER_SELECT + " WHERE f.guild_id = ? AND f.name = ? GROUP BY f.id",
            (guild_id, name)).fetchone()
        if row:
            return _folder(row)
        count = self._db.execute(
            "SELECT COUNT(*) FROM folders WHERE guild_id = ?", (guild_id,)).fetchone()[0]
        if count >= MAX_FOLDERS:
            raise FolderLimitError(f"A server can have at most {MAX_FOLDERS} folders.")
        cur = self._db.execute(
            "INSERT INTO folders (guild_id, name) VALUES (?, ?)", (guild_id, name))
        return Folder(cur.lastrowid, guild_id, name, 0, 0)

    def get_or_create_folder(self, guild_id: int, name: str) -> Folder:
        with self._db:
            return self._get_or_create_folder(guild_id, name)

    def get_folder(self, guild_id: int, folder_id: int) -> Optional[Folder]:
        row = self._db.execute(
            _FOLDER_SELECT + " WHERE f.guild_id = ? AND f.id = ? GROUP BY f.id",
            (guild_id, folder_id)).fetchone()
        return _folder(row) if row else None

    def folders(self, guild_id: int) -> list[Folder]:
        rows = self._db.execute(
            _FOLDER_SELECT + " WHERE f.guild_id = ? GROUP BY f.id "
            "ORDER BY f.name COLLATE NOCASE", (guild_id,))
        return [_folder(r) for r in rows]

    def rename_folder(self, guild_id: int, folder_id: int, name: str) -> Optional[Folder]:
        name = self._clean_name(name)
        try:
            with self._db:
                cur = self._db.execute(
                    "UPDATE folders SET name = ? WHERE guild_id = ? AND id = ?",
                    (name, guild_id, folder_id))
        except sqlite3.IntegrityError:
            raise FolderExistsError(name) from None
        return self.get_folder(guild_id, folder_id) if cur.rowcount else None

    def set_folder_style(self, guild_id: int, folder_id: int, icon: Optional[str],
                         color: Optional[str]) -> Optional[Folder]:
        """Set a folder's optional icon/color. None resets that part to default."""
        if icon is not None and icon not in ICON_KEYS:
            raise ValueError(f"unknown icon {icon!r}")
        if color is not None and color not in COLOR_KEYS:
            raise ValueError(f"unknown color {color!r}")
        with self._db:
            cur = self._db.execute(
                "UPDATE folders SET icon = ?, color = ? WHERE guild_id = ? AND id = ?",
                (icon, color, guild_id, folder_id))
        return self.get_folder(guild_id, folder_id) if cur.rowcount else None

    def delete_folder(self, guild_id: int, folder_id: int) -> int:
        """Delete a folder and its tasks. Returns how many tasks were deleted."""
        with self._db:
            n = self._db.execute(
                "DELETE FROM notes WHERE guild_id = ? AND folder_id = ?",
                (guild_id, folder_id)).rowcount
            self._db.execute(
                "DELETE FROM folders WHERE guild_id = ? AND id = ?", (guild_id, folder_id))
        return n

    # -- create ---------------------------------------------------------
    def _insert(self, guild_id: int, folder_id: int, text: str, author_id: int) -> int:
        row = self._db.execute(
            "SELECT next_id FROM counters WHERE guild_id = ?", (guild_id,)).fetchone()
        note_id = row["next_id"] if row else 1
        self._db.execute(
            "INSERT INTO counters (guild_id, next_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET next_id = excluded.next_id",
            (guild_id, note_id + 1))
        self._db.execute(
            "INSERT INTO notes (guild_id, id, text, folder_id, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, note_id, text.strip()[:MAX_TASK_LEN], folder_id, author_id, int(time.time())))
        return note_id

    def add(self, guild_id: int, text: str, author_id: int,
            folder_name: Optional[str] = None) -> Note:
        with self._db:
            folder = self._get_or_create_folder(guild_id, folder_name or DEFAULT_FOLDER)
            note_id = self._insert(guild_id, folder.id, text, author_id)
        return self.get(guild_id, note_id)  # type: ignore[return-value]

    def add_many(self, guild_id: int, folder_id: int, texts: Iterable[str],
                 author_id: int) -> int:
        """Add one task per text to a folder. Returns how many were added."""
        n = 0
        with self._db:
            for t in texts:
                if t.strip():
                    self._insert(guild_id, folder_id, t, author_id)
                    n += 1
        return n

    # -- read -----------------------------------------------------------
    def get(self, guild_id: int, note_id: int) -> Optional[Note]:
        row = self._db.execute(
            _NOTE_SELECT + " WHERE n.guild_id = ? AND n.id = ?", (guild_id, note_id)).fetchone()
        return _note(row) if row else None

    @staticmethod
    def _status_sql(status: str) -> str:
        return {"open": " AND n.done = 0", "done": " AND n.done = 1"}.get(status, "")

    def list(self, guild_id: int, status: str = "all",
             folder_id: Optional[int] = None) -> list[Note]:
        sql = _NOTE_SELECT + " WHERE n.guild_id = ?" + self._status_sql(status)
        params: list = [guild_id]
        if folder_id is not None:
            sql += " AND n.folder_id = ?"
            params.append(folder_id)
        sql += " ORDER BY n.id"
        return [_note(r) for r in self._db.execute(sql, params)]

    def search(self, guild_id: int, query: str, status: str = "all") -> list[Note]:
        like = f"%{_escape_like(query.strip())}%"
        sql = (_NOTE_SELECT + " WHERE n.guild_id = ? "
               "AND (n.text LIKE ? ESCAPE '\\' OR f.name LIKE ? ESCAPE '\\')"
               + self._status_sql(status) + " ORDER BY n.id")
        return [_note(r) for r in self._db.execute(sql, (guild_id, like, like))]

    def progress(self, guild_id: int, folder_id: Optional[int] = None) -> tuple[int, int]:
        """Return (done, total)."""
        notes = self.list(guild_id, "all", folder_id)
        return sum(n.done for n in notes), len(notes)

    # -- update ---------------------------------------------------------
    def set_done(self, guild_id: int, note_id: int, done: bool,
                 user_id: int) -> Optional[Note]:
        changed = self.set_many_done(guild_id, [note_id] if done else [],
                                     [] if done else [note_id], user_id)
        return self.get(guild_id, note_id) if changed else None

    def set_many_done(self, guild_id: int, check_ids: Iterable[int],
                      uncheck_ids: Iterable[int], user_id: int) -> int:
        """Check/uncheck tasks in bulk. Returns the number of rows touched."""
        now = int(time.time())
        n = 0
        with self._db:
            for nid in check_ids:
                n += self._db.execute(
                    "UPDATE notes SET done = 1, done_by = COALESCE(CASE WHEN done = 1 THEN done_by END, ?), "
                    "done_at = COALESCE(CASE WHEN done = 1 THEN done_at END, ?) "
                    "WHERE guild_id = ? AND id = ?", (user_id, now, guild_id, nid)).rowcount
            for nid in uncheck_ids:
                n += self._db.execute(
                    "UPDATE notes SET done = 0, done_by = NULL, done_at = NULL "
                    "WHERE guild_id = ? AND id = ?", (guild_id, nid)).rowcount
        return n

    # -- delete ---------------------------------------------------------
    def remove(self, guild_id: int, note_id: int) -> Optional[Note]:
        note = self.get(guild_id, note_id)
        if note is not None:
            self.remove_many(guild_id, [note_id])
        return note

    def remove_many(self, guild_id: int, note_ids: Iterable[int]) -> int:
        with self._db:
            return sum(self._db.execute(
                "DELETE FROM notes WHERE guild_id = ? AND id = ?", (guild_id, nid)).rowcount
                for nid in note_ids)

    def clear_done(self, guild_id: int, folder_id: Optional[int] = None) -> int:
        sql = "DELETE FROM notes WHERE guild_id = ? AND done = 1"
        params: list = [guild_id]
        if folder_id is not None:
            sql += " AND folder_id = ?"
            params.append(folder_id)
        with self._db:
            return self._db.execute(sql, params).rowcount
