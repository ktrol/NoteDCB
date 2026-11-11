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
