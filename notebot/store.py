"""SQLite-backed storage for meeting notes.

Each Discord server (guild) gets its own sequence of note numbers (#1, #2, ...)
so people can refer to notes with short, stable IDs.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    guild_id   INTEGER NOT NULL,
    id         INTEGER NOT NULL,
    text       TEXT    NOT NULL,
    meeting    TEXT,
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


@dataclass(frozen=True)
class Note:
    guild_id: int
    id: int
    text: str
    meeting: Optional[str]
    done: bool
    created_by: int
    created_at: int
    done_by: Optional[int]
    done_at: Optional[int]


def _row_to_note(row: sqlite3.Row) -> Note:
    return Note(
        guild_id=row["guild_id"],
        id=row["id"],
        text=row["text"],
        meeting=row["meeting"],
        done=bool(row["done"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        done_by=row["done_by"],
        done_at=row["done_at"],
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class NoteStore:
    def __init__(self, path: str = "notes.db") -> None:
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- create ---------------------------------------------------------
    def add(self, guild_id: int, text: str, author_id: int,
            meeting: Optional[str] = None) -> Note:
        meeting = meeting.strip() or None if meeting else None
        with self._db:
            row = self._db.execute(
                "SELECT next_id FROM counters WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            note_id = row["next_id"] if row else 1
            self._db.execute(
                "INSERT INTO counters (guild_id, next_id) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET next_id = excluded.next_id",
                (guild_id, note_id + 1),
            )
            self._db.execute(
                "INSERT INTO notes (guild_id, id, text, meeting, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, note_id, text.strip(), meeting, author_id, int(time.time())),
            )
        return self.get(guild_id, note_id)  # type: ignore[return-value]

    # -- read -----------------------------------------------------------
    def get(self, guild_id: int, note_id: int) -> Optional[Note]:
        row = self._db.execute(
            "SELECT * FROM notes WHERE guild_id = ? AND id = ?", (guild_id, note_id)
        ).fetchone()
        return _row_to_note(row) if row else None

    def list(self, guild_id: int, status: str = "all",
             meeting: Optional[str] = None) -> list[Note]:
        """status is one of: all, open, done."""
        sql = "SELECT * FROM notes WHERE guild_id = ?"
        params: list = [guild_id]
        if status == "open":
            sql += " AND done = 0"
        elif status == "done":
            sql += " AND done = 1"
        if meeting:
            sql += " AND meeting = ? COLLATE NOCASE"
            params.append(meeting.strip())
        sql += " ORDER BY id"
        return [_row_to_note(r) for r in self._db.execute(sql, params)]

    def search(self, guild_id: int, query: str, status: str = "all") -> list[Note]:
        like = f"%{_escape_like(query.strip())}%"
        sql = ("SELECT * FROM notes WHERE guild_id = ? "
               "AND (text LIKE ? ESCAPE '\\' OR meeting LIKE ? ESCAPE '\\')")
        params: list = [guild_id, like, like]
        if status == "open":
            sql += " AND done = 0"
        elif status == "done":
            sql += " AND done = 1"
        sql += " ORDER BY id"
        return [_row_to_note(r) for r in self._db.execute(sql, params)]

    def meetings(self, guild_id: int) -> list[str]:
        rows = self._db.execute(
            "SELECT DISTINCT meeting FROM notes WHERE guild_id = ? AND meeting IS NOT NULL "
            "ORDER BY meeting COLLATE NOCASE", (guild_id,)
        )
        return [r["meeting"] for r in rows]

    def progress(self, guild_id: int, meeting: Optional[str] = None) -> tuple[int, int]:
        """Return (done, total)."""
        notes = self.list(guild_id, "all", meeting)
        return sum(n.done for n in notes), len(notes)

    # -- update ---------------------------------------------------------
    def set_done(self, guild_id: int, note_id: int, done: bool,
                 user_id: int) -> Optional[Note]:
        with self._db:
            cur = self._db.execute(
                "UPDATE notes SET done = ?, done_by = ?, done_at = ? "
                "WHERE guild_id = ? AND id = ?",
                (int(done), user_id if done else None,
                 int(time.time()) if done else None, guild_id, note_id),
            )
        return self.get(guild_id, note_id) if cur.rowcount else None

    # -- delete ---------------------------------------------------------
    def remove(self, guild_id: int, note_id: int) -> Optional[Note]:
        note = self.get(guild_id, note_id)
        if note is None:
            return None
        with self._db:
            self._db.execute(
                "DELETE FROM notes WHERE guild_id = ? AND id = ?", (guild_id, note_id)
            )
        return note

    def clear_done(self, guild_id: int, meeting: Optional[str] = None) -> int:
        sql = "DELETE FROM notes WHERE guild_id = ? AND done = 1"
        params: list = [guild_id]
        if meeting:
            sql += " AND meeting = ? COLLATE NOCASE"
            params.append(meeting.strip())
        with self._db:
            return self._db.execute(sql, params).rowcount
