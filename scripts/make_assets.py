"""Generate the bot avatar (1024x1024) and banner (1920x1080) into assets/.

Usage: python scripts/make_assets.py
Requires Pillow. Uses Segoe UI on Windows, falls back to DejaVu / default font.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets"
SS = 2  # supersampling factor for smooth edges

TEAL_DARK = (14, 52, 58)
TEAL = (46, 125, 110)
TEAL_LIGHT = (94, 196, 170)
INK = (16, 36, 44)
PAPER = (247, 250, 249)
GOLD = (242, 190, 80)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def gradient(size, top, bottom, diagonal=True) -> Image.Image:
    w, h = size
    base = Image.new("RGB", size, top)
    px = base.load()
    for y in range(h):
        for x in range(w):
            t = ((x / w + y / h) / 2) if diagonal else y / h
            px[x, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return base


def draw_mark(img: Image.Image, cx: float, cy: float, s: float) -> None:
    """Clipboard with checklist. s = overall height of the clipboard."""
    d = ImageDraw.Draw(img)
    w, h = s * 0.74, s
    x0, y0 = cx - w / 2, cy - h / 2
    r = s * 0.07
    # soft shadow
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x0 + s * 0.02, y0 + s * 0.035, x0 + w + s * 0.02, y0 + h + s * 0.035),
        r, fill=(0, 0, 0, 70))
    from PIL import ImageFilter
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s * 0.03)))
    d = ImageDraw.Draw(img)
    # board + paper
    d.rounded_rectangle((x0, y0, x0 + w, y0 + h), r, fill=INK)
    pad = s * 0.045
    d.rounded_rectangle((x0 + pad, y0 + pad * 2.6, x0 + w - pad, y0 + h - pad),
                        r * 0.6, fill=PAPER)
    # clip
    cw, ch = w * 0.42, s * 0.1
    d.rounded_rectangle((cx - cw / 2, y0 - ch * 0.35, cx + cw / 2, y0 + ch * 0.85),
                        ch * 0.35, fill=GOLD)
    d.rounded_rectangle((cx - cw * 0.22, y0 - ch * 0.05, cx + cw * 0.22, y0 + ch * 0.4),
                        ch * 0.2, fill=INK)
    # rows
    rows = [True, True, False]
    top = y0 + h * 0.20
    gap = h * 0.235
    bx = x0 + w * 0.16
    box = h * 0.115
    lw = max(2, round(s * 0.028))
    for i, checked in enumerate(rows):
        ry = top + i * gap
        d.rounded_rectangle((bx, ry, bx + box, ry + box), box * 0.22,
                            outline=TEAL, width=lw, fill=TEAL if checked else None)
        if checked:
            pts = [(bx + box * 0.22, ry + box * 0.55), (bx + box * 0.43, ry + box * 0.74),
                   (bx + box * 0.78, ry + box * 0.26)]
            d.line(pts, fill=PAPER, width=lw + 1, joint="curve")
        tx = bx + box + w * 0.07
        tw = w * (0.46 if i != 1 else 0.38)
        d.rounded_rectangle((tx, ry + box * 0.18, tx + tw, ry + box * 0.18 + box * 0.28),
                            box * 0.14, fill=(150, 170, 168) if checked else INK)
        d.rounded_rectangle((tx, ry + box * 0.66, tx + tw * 0.6, ry + box * 0.66 + box * 0.2),
                            box * 0.1, fill=(200, 214, 211))


def make_avatar() -> None:
    n = 1024 * SS
    bg = gradient((n, n), TEAL_LIGHT, TEAL_DARK).convert("RGBA")
    draw_mark(bg, n / 2, n / 2 + n * 0.01, n * 0.64)
    # circular safe-crop is applied by Discord; keep full square artwork.
    bg.convert("RGB").resize((1024, 1024), Image.LANCZOS).save(OUT / "avatar.png", optimize=True)


def make_banner() -> None:
    w, h = 1920 * SS, 1080 * SS
    img = gradient((w, h), TEAL_DARK, (8, 24, 30)).convert("RGBA")
    # subtle grid (drawn on an overlay so the low alpha actually blends)
    grid = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = 90 * SS
    for x in range(0, w, step):
        gd.line((x, 0, x, h), fill=(255, 255, 255, 9), width=SS)
    for y in range(0, h, step):
        gd.line((0, y, w, y), fill=(255, 255, 255, 9), width=SS)
    img.alpha_composite(grid)
    # accent glow
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((w * 0.62, -h * 0.2, w * 1.15, h * 0.8), fill=(*TEAL, 110))
    from PIL import ImageFilter
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120 * SS)))
    d = ImageDraw.Draw(img)

    # mark on the right
    draw_mark(img, w * 0.79, h * 0.5, h * 0.62)
    d = ImageDraw.Draw(img)

    # text on the left
    left = int(w * 0.08)
    top = h * 0.27
    d.rounded_rectangle((left + 8 * SS, top, left + 98 * SS, top + 8 * SS), 4 * SS, fill=GOLD)
    d.text((left, top + 30 * SS), "NoteDCB", font=font(210 * SS, True), fill=PAPER)
    d.text((left + 8 * SS, top + 330 * SS), "Meeting notes, checked off.",
           font=font(70 * SS), fill=TEAL_LIGHT)
    d.text((left + 8 * SS, top + 450 * SS),
           "Add  •  Check  •  Search  •  Stay on task",
           font=font(44 * SS), fill=(170, 195, 192))

    img.convert("RGB").resize((1920, 1080), Image.LANCZOS).save(OUT / "banner.png", optimize=True)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    make_avatar()
    make_banner()
    print("wrote", OUT / "avatar.png", "and", OUT / "banner.png")
