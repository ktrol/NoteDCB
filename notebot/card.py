"""Render the checklist as a frosted-glass card (PNG).

Discord draws its own buttons and menus and can't be themed, so the "glass"
look lives in this image, which is shown inside the message embed.
Needs Pillow. Uses Segoe UI on Windows and falls back to DejaVu/Arial.
"""
from __future__ import annotations

import functools
import io
from dataclasses import dataclass, field
from typing import Union

from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 2            # supersampling; the final image is downscaled for smooth edges
W = 720          # final width in px
M = 20           # outer margin around the glass panel
PAD = 30         # padding inside the panel
LINE_H = 28
MAX_LINES = 2

BG_TOP = (10, 40, 48)
BG_BOTTOM = (6, 20, 30)
TEAL = (94, 196, 170)
GOLD = (242, 190, 80)
BLUE = (80, 140, 220)
WHITE = (246, 251, 250)
MUTED = (170, 198, 195)
INK = (10, 40, 48)


@dataclass
class TaskRow:
    text: str
    done: bool = False


@dataclass
class FolderRow:
    name: str
    done: int = 0
    total: int = 0


@dataclass
class Card:
    title: str
    subtitle: str = ""
    done: int = 0
    total: int = 0
    rows: list[Union[TaskRow, FolderRow]] = field(default_factory=list)
    empty_text: str = "Nothing here yet"
    show_progress: bool = True


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


def _folder_icon(d: ImageDraw.ImageDraw, x: float, y: float, s: float, fill) -> None:
    d.rounded_rectangle((P(x), P(y + s * 0.18), P(x + s * 0.42), P(y + s * 0.5)), P(3), fill=fill)
    d.rounded_rectangle((P(x), P(y + s * 0.3), P(x + s), P(y + s * 0.92)), P(5), fill=fill)


def render_card(card: Card) -> bytes:
    f_title, f_sub = font(34, True), font(19)
    f_text, f_small = font(22), font(18, True)

    inner_w = W - 2 * M - 2 * PAD
    text_x_off = 30 + 16  # checkbox + gap
    text_w = P(inner_w - text_x_off)

    # ---- layout pass: row heights
    laid: list[tuple[Union[TaskRow, FolderRow], list[str], float]] = []
    for r in card.rows:
        if isinstance(r, TaskRow):
            lines = wrap(f_text, r.text, text_w)
            laid.append((r, lines, max(52, 24 + LINE_H * len(lines))))
        else:
            laid.append((r, [], 58))
    rows_h = sum(h for _, _, h in laid) or 70

    head_h = 28 + 40 + (26 if card.subtitle else 0)
    bar_h = 16 + 26 if card.show_progress else 8
    H = M + head_h + bar_h + 18 + 2 + 10 + rows_h + 20 + M
    size = (P(W), P(H))

    # ---- background with a few hard-edged shapes so the frosting is visible
    bg = _gradient(size, BG_TOP, BG_BOTTOM)
    shapes = Image.new("RGBA", size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shapes)
    sd.ellipse((P(W * 0.55), P(-60), P(W * 1.15), P(190)), fill=(*TEAL, 120))
    sd.ellipse((P(-90), P(H * 0.35), P(130), P(H * 0.35 + 220)), fill=(*BLUE, 105))
    sd.ellipse((P(W * 0.6), P(H - 170), P(W * 1.05), P(H + 40)), fill=(*GOLD, 85))
    bg.alpha_composite(shapes)

    # ---- glass panel
    box = (P(M), P(M), P(W - M), P(H - M))
    radius = P(30)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)

    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (box[0], box[1] + P(8), box[2], box[3] + P(8)), radius, fill=(0, 0, 0, 110))
    img = bg.copy()
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(P(14))))

    frosted = img.filter(ImageFilter.GaussianBlur(P(16)))
    img.paste(frosted, mask=mask)

    tint = Image.new("RGBA", size, (255, 255, 255, 30))
    img.paste(Image.alpha_composite(img, tint), mask=mask)

    hl = Image.new("RGBA", size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    span = int((box[3] - box[1]) * 0.45)
    for k in range(span):
        a = round(46 * (1 - k / span) ** 2)
        hd.line((box[0], box[1] + k, box[2], box[1] + k), fill=(255, 255, 255, a))
    hl.putalpha(Image.composite(hl.getchannel("A"), Image.new("L", size, 0), mask))
    img.alpha_composite(hl)

    border = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(box, radius, outline=(255, 255, 255, 85), width=P(2))
    img.alpha_composite(border)

    # ---- translucent details (tracks, dividers, checkbox outlines) on one layer
    glass = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)
    x0 = M + PAD
    x1 = W - M - PAD
    y = M + 28 + 40 + (26 if card.subtitle else 0) + 16
    bar_y = y
    if card.show_progress:
        gd.rounded_rectangle((P(x0), P(bar_y), P(x1 - 70), P(bar_y + 12)), P(6), fill=(255, 255, 255, 55))
    y += 26 if card.show_progress else 0
    div_y = y + 18
    gd.rectangle((P(x0), P(div_y), P(x1), P(div_y) + P(1)), fill=(255, 255, 255, 60))
    row_top = div_y + 10

    cy = row_top
    for r, lines, h in laid:
        if isinstance(r, TaskRow):
            if not r.done:
                gd.rounded_rectangle((P(x0), P(cy + (h - 28) / 2), P(x0 + 28), P(cy + (h - 28) / 2 + 28)),
                                     P(8), outline=(255, 255, 255, 175), width=P(2.2),
                                     fill=(255, 255, 255, 22))
        else:
            tx1 = x1
            gd.rounded_rectangle((P(tx1 - 150), P(cy + h / 2 - 6), P(tx1 - 52), P(cy + h / 2 + 6)),
                                 P(6), fill=(255, 255, 255, 55))
        cy += h
        gd.rectangle((P(x0 + text_x_off - 2), P(cy), P(x1), P(cy) + P(1)), fill=(255, 255, 255, 22))
    img.alpha_composite(glass)

    # ---- opaque drawing: text, fills, checks
    d = ImageDraw.Draw(img)
    d.text((P(x0), P(M + 28)), _ellipsize(f_title, card.title, P(inner_w)), font=f_title, fill=WHITE)
    if card.subtitle:
        d.text((P(x0 + 2), P(M + 28 + 46)), _ellipsize(f_sub, card.subtitle, P(inner_w)),
               font=f_sub, fill=MUTED)
    if card.show_progress:
        frac = card.done / card.total if card.total else 0
        track_w = (x1 - 70) - x0
        if frac > 0:
            d.rounded_rectangle((P(x0), P(bar_y), P(x0 + max(12, track_w * frac)), P(bar_y + 12)),
                                P(6), fill=TEAL)
        pct = f"{round(frac * 100)}%"
        d.text((P(x1), P(bar_y + 6)), pct, font=f_small, fill=WHITE, anchor="rm")

    cy = row_top
    if not laid:
        d.text((P((x0 + x1) / 2), P(cy + 35)), card.empty_text, font=f_text, fill=MUTED, anchor="mm")
    for r, lines, h in laid:
        if isinstance(r, TaskRow):
            bx, by = x0, cy + (h - 28) / 2
            if r.done:
                d.rounded_rectangle((P(bx), P(by), P(bx + 28), P(by + 28)), P(8), fill=TEAL)
                d.line([(P(bx + 7), P(by + 15)), (P(bx + 12.5), P(by + 20)), (P(bx + 21.5), P(by + 8.5))],
                       fill=INK, width=P(3.2), joint="curve")
            ty = cy + (h - LINE_H * len(lines)) / 2
            for i, line in enumerate(lines):
                color = MUTED if r.done else WHITE
                tx = x0 + text_x_off
                d.text((P(tx), P(ty + i * LINE_H) + P(1)), line, font=f_text, fill=color)
                if r.done:
                    mid = P(ty + i * LINE_H + LINE_H / 2 + 2)
                    d.line((P(tx), mid, P(tx) + _width(f_text, line), mid), fill=MUTED, width=P(1.6))
        else:
            _folder_icon(d, x0 + 1, cy + h / 2 - 12, 26, GOLD)
            name = _ellipsize(f_text, r.name, P(inner_w - text_x_off - 170))
            d.text((P(x0 + text_x_off), P(cy + h / 2)), name, font=f_text, fill=WHITE, anchor="lm")
            frac = r.done / r.total if r.total else 0
            if frac > 0:
                d.rounded_rectangle((P(x1 - 150), P(cy + h / 2 - 6),
                                     P(x1 - 150 + max(12, 98 * frac)), P(cy + h / 2 + 6)), P(6), fill=TEAL)
            d.text((P(x1), P(cy + h / 2)), f"{r.done}/{r.total}", font=f_small, fill=MUTED, anchor="rm")
        cy += h

    out = img.convert("RGB").resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
