"""Render the checklist as a glass card (transparent PNG, no backdrop).

Discord draws its own buttons and menus and can't be themed, so the "glass"
look lives in this image, which is sent as the message's attachment.
The card is sized for Discord's image display box (about 550x400), so only
a handful of rows fit per card; the UI pages through the rest.
Needs Pillow. Uses Segoe UI on Windows and falls back to DejaVu/Arial.
"""
from __future__ import annotations

import functools
import io
import math
from dataclasses import dataclass, field
from typing import Optional, Union

from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 2            # rendered at 2x so it stays sharp on high-DPI screens
W = 520          # logical width; the PNG is W*S px wide
M = 8            # outer margin around the glass panel
PAD = 18         # padding inside the panel
LINE_H = 21
MAX_LINES = 2

BG_TOP = (10, 40, 48)
BG_BOTTOM = (6, 20, 30)
TEAL = (94, 196, 170)
GOLD = (242, 190, 80)
BLUE = (80, 140, 220)
WHITE = (246, 251, 250)
MUTED = (170, 198, 195)
INK = (10, 40, 48)


COLORS = {
    "gold": (242, 190, 80), "teal": (94, 196, 170), "blue": (105, 165, 245),
    "purple": (178, 140, 235), "pink": (242, 135, 185), "red": (236, 100, 100),
    "orange": (246, 152, 72), "green": (135, 205, 105), "grey": (178, 188, 198),
}


@dataclass
class TaskRow:
    text: str
    done: bool = False


@dataclass
class FolderRow:
    name: str
    done: int = 0
    total: int = 0
    icon: Optional[str] = None
    color: Optional[str] = None


@dataclass
class Card:
    title: str
    subtitle: str = ""
    done: int = 0
    total: int = 0
    rows: list[Union[TaskRow, FolderRow]] = field(default_factory=list)
    empty_text: str = "Nothing here yet"
    show_progress: bool = True
    icon: Optional[str] = None   # header icon key (None = no header icon)
    color: Optional[str] = None


def P(v: float) -> int:
    return round(v * S)


@functools.lru_cache(maxsize=16)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, P(size))
        except OSError:
            continue
    return ImageFont.load_default(P(size))


def _width(f: ImageFont.FreeTypeFont, text: str) -> float:
    return f.getlength(text)


def _ellipsize(f, text: str, max_w: float) -> str:
    if _width(f, text) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:  # longest prefix that still fits with the ellipsis
        mid = (lo + hi + 1) // 2
        if _width(f, text[:mid].rstrip() + "…") <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def wrap(f, text: str, max_w: float, max_lines: int = MAX_LINES) -> list[str]:
    """Wrap to at most max_lines (breaking at spaces when possible); ellipsize if cut."""
    rest = " ".join(text.split())[:400]
    lines: list[str] = []
    while rest and len(lines) < max_lines:
        if _width(f, rest) <= max_w:
            lines.append(rest)
            rest = ""
            break
        lo, hi = 1, len(rest)
        while lo < hi:  # longest prefix that fits
            mid = (lo + hi + 1) // 2
            if _width(f, rest[:mid]) <= max_w:
                lo = mid
            else:
                hi = mid - 1
        k = lo
        sp = rest.rfind(" ", 0, k + 1)
        if sp > 0:
            k = sp
        lines.append(rest[:k].rstrip())
        rest = rest[k:].lstrip()
    if rest:
        lines[-1] = _ellipsize(f, lines[-1] + " " + rest, max_w)
    return lines or [""]


def _gradient(size, top, bottom) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line((0, y, w, y), fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    return img


def _shade(c, k: float):
    return tuple(max(0, min(255, round(v * k))) for v in c)


def draw_icon(d: ImageDraw.ImageDraw, key: Optional[str], x: float, y: float, sz: float,
              color: Optional[str] = None) -> None:
    """Draw a flat vector icon in a sz x sz box at (x, y) (logical px)."""
    c = COLORS.get(color or "gold", COLORS["gold"])
    dark = _shade(c, 0.55)
    e = lambda x0, y0, x1, y1: (P(x + x0 * sz), P(y + y0 * sz), P(x + x1 * sz), P(y + y1 * sz))
    pt = lambda px, py: (P(x + px * sz), P(y + py * sz))
    key = key or "folder"
    if key == "folder":
        d.rounded_rectangle(e(0, .18, .42, .5), P(3), fill=c)
        d.rounded_rectangle(e(0, .3, 1, .92), P(5), fill=c)
    elif key == "star":
        pts = []
        for i in range(10):
            r = .52 if i % 2 == 0 else .23
            ang = -math.pi / 2 + i * math.pi / 5
            pts.append(pt(.5 + r * math.cos(ang) * 1.0, .53 + r * math.sin(ang) * 1.0))
        d.polygon(pts, fill=c)
    elif key == "flag":
        d.rounded_rectangle(e(.12, .06, .22, .96), P(2), fill=dark)
        d.polygon([pt(.22, .1), pt(.92, .1), pt(.74, .36), pt(.92, .62), pt(.22, .62)], fill=c)
    elif key == "people":
        d.ellipse(e(.50, .12, .78, .40), fill=dark)
        d.rounded_rectangle(e(.40, .46, .90, .90), P(8), fill=dark)
        d.ellipse(e(.18, .10, .50, .42), fill=c)
        d.rounded_rectangle(e(.06, .48, .62, .94), P(9), fill=c)
    elif key == "calendar":
        d.rounded_rectangle(e(.04, .14, .96, .94), P(6), fill=c)
        d.rounded_rectangle(e(.04, .14, .96, .40), P(6), fill=dark)
        d.rectangle(e(.04, .30, .96, .40), fill=dark)
        for rx in (.28, .72):
            d.rounded_rectangle(e(rx - .06, .02, rx + .06, .26), P(2), fill=WHITE)
        for gy in (.52, .72):
            for gx in (.2, .44, .68):
                d.rounded_rectangle(e(gx, gy, gx + .14, gy + .12), P(2), fill=dark)
    elif key == "chat":
        d.rounded_rectangle(e(.04, .1, .96, .72), P(8), fill=c)
        d.polygon([pt(.2, .68), pt(.2, .95), pt(.46, .7)], fill=c)
        for dx in (.28, .5, .72):
            d.ellipse(e(dx - .06, .36, dx + .06, .48), fill=dark)
    elif key == "target":
        for r, col in ((.48, c), (.34, dark), (.21, c), (.09, dark)):
            d.ellipse(e(.5 - r, .5 - r, .5 + r, .5 + r), fill=col)
    elif key == "heart":
        d.ellipse(e(.06, .12, .54, .6), fill=c)
        d.ellipse(e(.46, .12, .94, .6), fill=c)
        d.polygon([pt(.09, .46), pt(.91, .46), pt(.5, .94)], fill=c)
    else:  # unknown key: plain folder
        draw_icon(d, "folder", x, y, sz, color)


def render_card(card: Card) -> bytes:
    f_title, f_sub = font(24, True), font(14)
    f_text, f_small = font(17), font(14, True)

    inner_w = W - 2 * M - 2 * PAD
    text_x_off = 34                      # checkbox + gap
    text_w = P(inner_w - text_x_off)

    # ---- layout pass: row heights
    laid: list[tuple[Union[TaskRow, FolderRow], list[str], float]] = []
    for r in card.rows:
        if isinstance(r, TaskRow):
            lines = wrap(f_text, r.text, text_w)
            laid.append((r, lines, max(34, 14 + LINE_H * len(lines))))
        else:
            laid.append((r, [], 32))
    rows_h = sum(h for _, _, h in laid) or 56

    top = M + 16
    bar_y = top + 38
    div_y = bar_y + 8 + 14 if card.show_progress else top + 40
    row_top = div_y + 6
    H = row_top + rows_h + 14 + M
    size = (P(W), P(H))
    x0, x1 = M + PAD, W - M - PAD

    # ---- transparent canvas + glass panel (no backdrop)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    box = (P(M), P(M), P(W - M), P(H - M))
    radius = P(20)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    zero = Image.new("L", size, 0)

    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (box[0], box[1] + P(3), box[2], box[3] + P(3)), radius, fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(P(4))))

    # smoky glass body: translucent dark teal that stays readable on any theme
    body = Image.new("RGBA", size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(body)
    top_y, bot_y = box[1], box[3]
    for y in range(top_y, bot_y):
        t = (y - top_y) / max(1, bot_y - top_y)
        bd.line((box[0], y, box[2], y),
                fill=(round(26 - 12 * t), round(56 - 24 * t), round(64 - 24 * t), round(172 + 30 * t)))
    body.putalpha(Image.composite(body.getchannel("A"), zero, mask))
    img.alpha_composite(body)

    # light sheen across the top, like light catching the glass
    sheen = Image.new("RGBA", size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    span = int((box[3] - box[1]) * 0.5)
    for k in range(span):
        sd.line((box[0], box[1] + k, box[2], box[1] + k),
                fill=(255, 255, 255, round(54 * (1 - k / span) ** 2)))
    sheen.putalpha(Image.composite(sheen.getchannel("A"), zero, mask))
    img.alpha_composite(sheen)

    border = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(box, radius, outline=(255, 255, 255, 95), width=P(1.5))
    img.alpha_composite(border)

    # ---- translucent details (tracks, dividers, checkbox outlines)
    glass = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)
    if card.show_progress:
        gd.rounded_rectangle((P(x0), P(bar_y), P(x1 - 46), P(bar_y + 8)), P(4), fill=(255, 255, 255, 60))
    gd.rectangle((P(x0), P(div_y), P(x1), P(div_y) + P(1)), fill=(255, 255, 255, 65))
    cy = row_top
    for r, lines, h in laid:
        if isinstance(r, TaskRow):
            if not r.done:
                by = cy + (h - 20) / 2
                gd.rounded_rectangle((P(x0), P(by), P(x0 + 20), P(by + 20)), P(6),
                                     outline=(255, 255, 255, 190), width=P(1.8),
                                     fill=(255, 255, 255, 24))
        else:
            gd.rounded_rectangle((P(x1 - 124), P(cy + h / 2 - 4), P(x1 - 44), P(cy + h / 2 + 4)),
                                 P(4), fill=(255, 255, 255, 60))
        cy += h
        gd.rectangle((P(x0 + text_x_off - 2), P(cy), P(x1), P(cy) + P(1)), fill=(255, 255, 255, 26))
    img.alpha_composite(glass)

    # ---- opaque drawing: text, fills, checks
    d = ImageDraw.Draw(img)
    head_x = x0
    if card.icon:
        draw_icon(d, card.icon, x0, top + 2, 26, card.color)
        head_x = x0 + 34
    sub_w = _width(f_sub, card.subtitle) if card.subtitle else 0
    title_max = P(x1 - head_x) - (sub_w + P(12) if card.subtitle else 0)
    d.text((P(head_x), P(top)), _ellipsize(f_title, card.title, title_max), font=f_title, fill=WHITE)
    if card.subtitle:
        d.text((P(x1), P(top + 17)), card.subtitle, font=f_sub, fill=MUTED, anchor="rm")
    if card.show_progress:
        frac = card.done / card.total if card.total else 0
        track_w = (x1 - 46) - x0
        if frac > 0:
            d.rounded_rectangle((P(x0), P(bar_y), P(x0 + max(8, track_w * frac)), P(bar_y + 8)),
                                P(4), fill=TEAL)
        d.text((P(x1), P(bar_y + 4)), f"{round(frac * 100)}%", font=f_small, fill=WHITE, anchor="rm")

    cy = row_top
    if not laid:
        d.text((P((x0 + x1) / 2), P(cy + 28)), card.empty_text, font=f_text, fill=MUTED, anchor="mm")
    for r, lines, h in laid:
        if isinstance(r, TaskRow):
            bx, by = x0, cy + (h - 20) / 2
            if r.done:
                d.rounded_rectangle((P(bx), P(by), P(bx + 20), P(by + 20)), P(6), fill=TEAL)
                d.line([(P(bx + 5), P(by + 10.5)), (P(bx + 8.8), P(by + 14.2)), (P(bx + 15.2), P(by + 6))],
                       fill=INK, width=P(2.4), joint="curve")
            ty = cy + (h - LINE_H * len(lines)) / 2
            tx = x0 + text_x_off
            for i, line in enumerate(lines):
                d.text((P(tx), P(ty + i * LINE_H)), line, font=f_text, fill=MUTED if r.done else WHITE)
                if r.done:
                    mid = P(ty + i * LINE_H + LINE_H / 2 + 1)
                    d.line((P(tx), mid, P(tx) + _width(f_text, line), mid), fill=MUTED, width=P(1.3))
        else:
            draw_icon(d, r.icon, x0, cy + h / 2 - 11, 22, r.color)
            name = _ellipsize(f_text, r.name, P(inner_w - text_x_off - 130))
            d.text((P(x0 + text_x_off), P(cy + h / 2)), name, font=f_text, fill=WHITE, anchor="lm")
            frac = r.done / r.total if r.total else 0
            if frac > 0:
                d.rounded_rectangle((P(x1 - 124), P(cy + h / 2 - 4),
                                     P(x1 - 124 + max(8, 80 * frac)), P(cy + h / 2 + 4)), P(4), fill=TEAL)
            d.text((P(x1), P(cy + h / 2)), f"{r.done}/{r.total}", font=f_small, fill=MUTED, anchor="rm")
        cy += h

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
