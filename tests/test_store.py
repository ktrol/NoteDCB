import sqlite3

import pytest

from notebot.store import (
    MAX_FOLDERS, FolderExistsError, FolderLimitError, NoteStore, split_lines,
)


@pytest.fixture
def store():
    s = NoteStore(":memory:")
    yield s
    s.close()


def test_split_lines_makes_one_task_per_line_and_strips_bullets():
    raw = "First\n\n  - Second  \n* Third\n[ ] Fourth\n[x] Fifth\n-5 degrees\n   \n"
    assert split_lines(raw) == ["First", "Second", "Third", "Fourth", "Fifth", "-5 degrees"]
    assert split_lines("a\r\nb\r\n") == ["a", "b"]
    assert len(split_lines("x" * 900)[0]) == 500


def test_ids_are_per_guild_and_not_reused(store):
    a = store.add(1, "first", 10)
    b = store.add(1, "second", 10)
    other = store.add(2, "elsewhere", 10)
    assert (a.id, b.id, other.id) == (1, 2, 1)
    store.remove(1, 2)
    assert store.add(1, "third", 10).id == 3


def test_default_folder_and_case_insensitive_reuse(store):
    assert store.add(1, "a", 10).folder == "General"
    n = store.add(1, "b", 10, "Weekly Sync")
    again = store.add(1, "c", 10, "weekly sync")
    assert n.folder_id == again.folder_id
    assert [f.name for f in store.folders(1)] == ["General", "Weekly Sync"]


def test_add_many_from_notepad_text(store):
    folder = store.get_or_create_folder(1, "Kickoff")
    n = store.add_many(1, folder.id, split_lines("one\ntwo\nthree"), 10)
    assert n == 3
    assert [t.text for t in store.list(1, "all", folder.id)] == ["one", "two", "three"]
    f = store.get_folder(1, folder.id)
    assert (f.done, f.total) == (0, 3)


def test_check_uncheck_and_bulk(store):
    n = store.add(1, "ship it", 10)
    done = store.set_done(1, n.id, True, 11)
    assert done.done and done.done_by == 11 and done.done_at
    reopened = store.set_done(1, n.id, False, 11)
    assert not reopened.done and reopened.done_by is None
    assert store.set_done(1, 999, True, 11) is None

    for t in "abc":
        store.add(1, t, 10)
    ids = [x.id for x in store.list(1, "open")]
    store.set_many_done(1, ids[:2], ids[2:], 12)
    assert [x.done for x in store.list(1, "all")] == [True, True, False, False]
    # re-checking keeps the original completer
    store.set_many_done(1, ids[:1], [], 99)
    assert store.get(1, ids[0]).done_by == 12


def test_folder_stats_and_filters(store):
    a = store.get_or_create_folder(1, "A")
    b = store.get_or_create_folder(1, "B")
    store.add_many(1, a.id, ["x", "y"], 10)
    store.add_many(1, b.id, ["z"], 10)
    store.set_done(1, 1, True, 10)
    assert [n.id for n in store.list(1, "open")] == [2, 3]
    assert [n.id for n in store.list(1, "done")] == [1]
    assert [n.id for n in store.list(1, "all", b.id)] == [3]
    assert store.progress(1) == (1, 3)
    assert store.progress(1, a.id) == (1, 2)
    assert store.progress(2) == (0, 0)
    assert {f.name: (f.done, f.total) for f in store.folders(1)} == {"A": (1, 2), "B": (0, 1)}


def test_search_matches_text_and_folder_and_escapes_wildcards(store):
    store.add(1, "Update the Budget spreadsheet", 10)
    store.add(1, "Book room", 10, "Budget review")
    store.add(1, "100% coverage", 10)
    assert [n.id for n in store.search(1, "budget")] == [1, 2]
    assert [n.id for n in store.search(1, "%")] == [3]
    assert store.search(1, "_") == []
    assert store.search(2, "budget") == []


def test_remove_many_and_clear_done(store):
    a = store.get_or_create_folder(1, "X")
    b = store.get_or_create_folder(1, "Y")
    store.add_many(1, a.id, ["a", "c"], 10)
    store.add_many(1, b.id, ["b"], 10)
    for i in (1, 2, 3):
        store.set_done(1, i, True, 10)
    assert store.clear_done(1, a.id) == 2
    assert [n.id for n in store.list(1)] == [3]
    assert store.remove_many(1, [3, 3]) == 1
    assert store.remove(1, 3) is None


def test_rename_delete_and_limits(store):
    a = store.get_or_create_folder(1, "A")
    store.get_or_create_folder(1, "B")
    store.add_many(1, a.id, ["x", "y"], 10)
    with pytest.raises(FolderExistsError):
        store.rename_folder(1, a.id, "b")
    assert store.rename_folder(1, a.id, "  Alpha   Team ").name == "Alpha Team"
    assert store.delete_folder(1, a.id) == 2
    assert store.get_folder(1, a.id) is None and store.list(1) == []
    for i in range(MAX_FOLDERS):
        store.get_or_create_folder(2, f"f{i}")
    with pytest.raises(FolderLimitError):
        store.get_or_create_folder(2, "one too many")
    assert store.get_or_create_folder(2, "F0").name == "f0"  # existing still fine


def test_migrates_v1_database(tmp_path):
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE notes (guild_id INTEGER NOT NULL, id INTEGER NOT NULL, text TEXT NOT NULL,
            meeting TEXT, done INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL,
            created_at INTEGER NOT NULL, done_by INTEGER, done_at INTEGER, PRIMARY KEY (guild_id, id));
        CREATE TABLE counters (guild_id INTEGER PRIMARY KEY, next_id INTEGER NOT NULL);
        INSERT INTO notes VALUES (1, 1, 'old a', 'Weekly', 1, 5, 100, 5, 200);
        INSERT INTO notes VALUES (1, 2, 'old b', NULL, 0, 5, 101, NULL, NULL);
        INSERT INTO counters VALUES (1, 3);
    """)
    db.commit()
    db.close()
    s = NoteStore(path)
    notes = s.list(1)
    assert [(n.id, n.folder, n.done) for n in notes] == [(1, "Weekly", True), (2, "General", False)]
    assert s.add(1, "new", 5).id == 3
    s.close()
    NoteStore(path).close()  # reopening a migrated db is a no-op


def test_folder_style_is_optional_validated_and_resettable(store):
    f = store.get_or_create_folder(1, "Team")
    assert (f.icon, f.color) == (None, None)
    f = store.set_folder_style(1, f.id, "people", "purple")
    assert (f.icon, f.color) == ("people", "purple")
    assert store.set_folder_style(1, f.id, "star", None).color is None
    with pytest.raises(ValueError):
        store.set_folder_style(1, f.id, "skull", None)
    with pytest.raises(ValueError):
        store.set_folder_style(1, f.id, None, "neon")
    assert store.set_folder_style(1, f.id, None, None).icon is None
    assert store.set_folder_style(2, f.id, "star", None) is None  # other guild: no effect
    assert store.folders(1)[0].icon is None


def test_adds_style_columns_to_v2_database(tmp_path):
    path = str(tmp_path / "v2.db")
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE, UNIQUE (guild_id, name));
        CREATE TABLE notes (guild_id INTEGER NOT NULL, id INTEGER NOT NULL, text TEXT NOT NULL,
            folder_id INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL,
            created_at INTEGER NOT NULL, done_by INTEGER, done_at INTEGER, PRIMARY KEY (guild_id, id));
        CREATE TABLE counters (guild_id INTEGER PRIMARY KEY, next_id INTEGER NOT NULL);
        INSERT INTO folders (guild_id, name) VALUES (1, 'Old folder');
    """)
    db.commit()
    db.close()
    s = NoteStore(path)
    f = s.folders(1)[0]
    assert (f.name, f.icon, f.color) == ("Old folder", None, None)
    assert s.set_folder_style(1, f.id, "flag", "teal").icon == "flag"
    s.close()
    NoteStore(path).close()
