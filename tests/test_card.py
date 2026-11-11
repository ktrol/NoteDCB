import io

from PIL import Image

from notebot.card import Card, FolderRow, TaskRow, font, render_card, wrap


def _png(card):
    data = render_card(card)
    img = Image.open(io.BytesIO(data))
    assert img.format == "PNG" and img.width == 720
    return img


def test_task_card_renders():
    img = _png(Card("Weekly sync", "2 of 3", 2, 3,
                    [TaskRow("a", True), TaskRow("b " * 400), TaskRow("c")]))
    assert img.height > 200


def test_folder_and_empty_cards_render():
    _png(Card("Meeting notes", "1 folder", 1, 2, [FolderRow("A", 1, 2)]))
    _png(Card("Empty", "", 0, 0, [], empty_text="Nothing", show_progress=False))


def test_full_page_is_reasonable_size():
    img = _png(Card("Big", "", 0, 25, [TaskRow("word " * 200) for _ in range(25)]))
    assert img.height < 4000


def test_wrap_limits_lines_and_ellipsizes():
    f = font(22)
    lines = wrap(f, "word " * 200, 400)
    assert len(lines) == 2 and lines[-1].endswith("…")
    assert wrap(f, "short", 400) == ["short"]
    assert all(f.getlength(l) <= 400 for l in wrap(f, "x" * 300, 400))
    assert wrap(f, "", 400) == [""]


def test_every_icon_and_color_renders_and_is_covered():
    from notebot.card import COLORS
    from notebot.store import COLOR_KEYS, ICON_KEYS
    assert set(COLORS) == set(COLOR_KEYS)
    rows = [FolderRow(k, 1, 2, k, COLOR_KEYS[i % len(COLOR_KEYS)]) for i, k in enumerate(ICON_KEYS)]
    _png(Card("All icons", "", 0, 0, rows))
    for k in ICON_KEYS:
        _png(Card("Header", "", 0, 0, [TaskRow("x")], icon=k, color="blue"))
    _png(Card("Unknown falls back", "", 0, 0, [FolderRow("a", 0, 0, "nope", "nope")]))
