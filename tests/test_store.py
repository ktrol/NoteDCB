import pytest

from notebot.store import NoteStore


@pytest.fixture
def store():
    s = NoteStore(":memory:")
    yield s
    s.close()


def test_ids_are_per_guild_and_not_reused(store):
    a = store.add(1, "first", 10)
    b = store.add(1, "second", 10)
    other = store.add(2, "elsewhere", 10)
    assert (a.id, b.id, other.id) == (1, 2, 1)
    store.remove(1, 2)
    assert store.add(1, "third", 10).id == 3


def test_check_and_uncheck(store):
    n = store.add(1, "ship it", 10)
    done = store.set_done(1, n.id, True, 11)
    assert done.done and done.done_by == 11 and done.done_at
    reopened = store.set_done(1, n.id, False, 11)
    assert not reopened.done and reopened.done_by is None
    assert store.set_done(1, 999, True, 11) is None


def test_list_filters_and_progress(store):
    store.add(1, "a", 10, "Weekly")
    store.add(1, "b", 10, "weekly")
    store.add(1, "c", 10, "Budget")
    store.set_done(1, 1, True, 10)
    assert [n.id for n in store.list(1, "open")] == [2, 3]
    assert [n.id for n in store.list(1, "done")] == [1]
    assert [n.id for n in store.list(1, "all", "WEEKLY")] == [1, 2]
    assert store.progress(1) == (1, 3)
    assert store.progress(1, "weekly") == (1, 2)
    assert store.progress(2) == (0, 0)


def test_search_matches_text_and_meeting_and_escapes_wildcards(store):
    store.add(1, "Update the Budget spreadsheet", 10)
    store.add(1, "Book room", 10, "Budget review")
    store.add(1, "100% coverage", 10)
    assert [n.id for n in store.search(1, "budget")] == [1, 2]
    assert [n.id for n in store.search(1, "%")] == [3]
    assert store.search(1, "_") == []
    assert store.search(2, "budget") == []


def test_remove_and_clear_done(store):
    store.add(1, "a", 10, "X")
    store.add(1, "b", 10, "Y")
    store.add(1, "c", 10, "X")
    for i in (1, 2, 3):
        store.set_done(1, i, True, 10)
    assert store.clear_done(1, "x") == 2
    assert [n.id for n in store.list(1)] == [2]
    assert store.remove(1, 2).text == "b"
    assert store.remove(1, 2) is None


def test_meetings_distinct_and_blank_meeting_ignored(store):
    store.add(1, "a", 10, "Sync")
    store.add(1, "b", 10, "Sync")
    store.add(1, "c", 10, "   ")
    assert store.meetings(1) == ["Sync"]
