import asyncio
import types

from notebot.store import NoteStore
from notebot.ui import NotesView


class _Resp:
    def __init__(self):
        self.edits = []

    async def edit_message(self, **kw):
        self.edits.append(kw)


def _interaction():
    return types.SimpleNamespace(
        response=_Resp(), user=types.SimpleNamespace(id=7),
        permissions=types.SimpleNamespace(manage_messages=True, administrator=False))


def _check_limits(view):
    rows = {}
    for c in view.children:
        rows.setdefault(c.row, []).append(c)
    assert len(rows) <= 5
    for items in rows.values():
        assert sum(5 if hasattr(c, "options") else 1 for c in items) <= 5
    for c in view.children:
        if hasattr(c, "options"):
            assert 1 <= len(c.options) <= 25


def test_home_pages_through_many_folders():
    async def run():
        store = NoteStore(":memory:")
        for i in range(60):
            store.get_or_create_folder(1, f"Folder {i:02d}")
        view = NotesView(store, 1)
        view.render()
        _check_limits(view)
        select = next(c for c in view.children if hasattr(c, "options"))
        assert len(select.options) == 25
        assert len(view._card.rows) == 25

        seen = {o.label for o in select.options}
        for _ in range(2):
            await view._on_home_next(_interaction())
            _check_limits(view)
            select = next(c for c in view.children if hasattr(c, "options"))
            seen |= {o.label for o in select.options}
        assert len(seen) == 60                       # every folder is reachable
        assert view.home_page == 2 and len(select.options) == 10
        nxt = [c for c in view.children if getattr(c, "emoji", None) and str(c.emoji) == "▶️"][0]
        assert nxt.disabled                          # last page

        await view._on_home_prev(_interaction())
        assert view.home_page == 1

        # deleting folders so there are fewer pages must not leave a stale page
        for f in store.folders(1)[:45]:
            store.delete_folder(1, f.id)
        view.render()
        assert view.home_page == 0
        _check_limits(view)
        store.close()
    asyncio.run(run())


def test_single_page_has_no_paging_buttons():
    async def run():
        store = NoteStore(":memory:")
        store.get_or_create_folder(1, "Only")
        view = NotesView(store, 1)
        view.render()
        labels = [c for c in view.children if not hasattr(c, "options")]
        assert len(labels) == 2                      # New folder, Search
    asyncio.run(run())
