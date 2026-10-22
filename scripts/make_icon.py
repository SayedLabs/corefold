"""Draws Corefold's icon and installer pictures from the Corefold mark: a rounded tile with its top
corner folded over and a round "core" in the middle (see the Corefold design system).

Makes assets/icon.png, assets/icon.ico, docs/icon.png and the installer's wizard*.bmp.
Run: python scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CORE = (10, 119, 104)        # core  #0A7768
CORE_SOFT = (220, 241, 237)  # core-soft #DCF1ED
SURFACE = (245, 247, 247)    # surface #F5F7F7
WHITE = (255, 255, 255)
S = 1024                     # drawn big, then made smaller (smooth edges)


def app_icon(size: int) -> Image.Image:
    """The mark as an app icon: the same shape, but solid (a light fold and a white core), so it
    reads the same on a light or a dark taskbar."""
    img = mark(CORE, S, hole=False, fold_alpha=0)
    u = S / 64
    d = ImageDraw.Draw(img)
    d.polygon([(40 * u, 4 * u), (60 * u, 24 * u), (40 * u, 24 * u)], fill=(122, 190, 179, 255))
    d.ellipse(((31 - 9) * u, (38 - 9) * u, (31 + 9) * u, (38 + 9) * u), fill=WHITE + (255,))
    return img.resize((size, size), Image.LANCZOS)


def mark(color, size: int, fold_alpha: float = 0.55, hole: bool = True) -> Image.Image:
    """The mark on a transparent square. Geometry on a 64-unit grid: tile 4..60 with 14 rounded
    corners; notch 36..60 × 4..28; fold triangle (40,4) (60,24) (40,24); core circle at (31,38) r 9."""
    u = S / 64
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    body = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(body)
    d.rounded_rectangle((4 * u, 4 * u, 60 * u, 60 * u), radius=14 * u, fill=255)
    d.rectangle((36 * u, 0, S, 28 * u), fill=0)                            # the notch
    if hole:
        d.ellipse(((31 - 9) * u, (38 - 9) * u, (31 + 9) * u, (38 + 9) * u), fill=0)  # the core
    img.paste(color + (255,), (0, 0), body)
    fold = Image.new("L", (S, S), 0)
    ImageDraw.Draw(fold).polygon([(40 * u, 4 * u), (60 * u, 24 * u), (40 * u, 24 * u)], fill=round(255 * fold_alpha))
    layer = Image.new("RGBA", (S, S), color + (0,))
    layer.putalpha(fold)
    img.alpha_composite(layer)
    return img.resize((size, size), Image.LANCZOS)


def on_background(bg, mark_img: Image.Image, size: tuple[int, int], at: tuple[int, int]) -> Image.Image:
    out = Image.new("RGBA", size, bg + (255,))
    out.alpha_composite(mark_img, at)
    return out.convert("RGB")


def wizard(scale: int) -> Image.Image:
    """The tall picture on the installer's first and last pages: the mark in white on teal, with a
    quiet pattern of small folded tiles."""
    w, h = 164 * scale, 314 * scale
    out = Image.new("RGBA", (w, h), CORE + (255,))
    small = mark((255, 255, 255), 18 * scale, fold_alpha=0.5, hole=False)
    faint = Image.new("RGBA", small.size, (0, 0, 0, 0))
    faint.paste(small, (0, 0))
    alpha = faint.getchannel("A").point(lambda a: a * 0.07)
    faint.putalpha(alpha)
    step = 30 * scale
    for row, y in enumerate(range(10 * scale, h, step)):
        for x in range(10 * scale + (row % 2) * step // 2, w, step):
            out.alpha_composite(faint, (x, y))
    big = mark(WHITE, 104 * scale, fold_alpha=0.55)
    out.alpha_composite(big, ((w - big.width) // 2, (h - big.height) // 2 - 20 * scale))
    return out.convert("RGB")


def main():
    icon = app_icon(512)
    icon.save(ASSETS / "icon.png")
    mark(CORE, 512).save(ROOT / "docs" / "icon.png")  # the website uses the brand mark itself
    big = app_icon(256)
    big.save(ASSETS / "icon.ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64),
                                          (128, 128), (256, 256)])
    for scale, name in ((1, "wizard.bmp"), (2, "wizard@2x.bmp")):
        wizard(scale).save(ASSETS / name)
    for scale, name in ((1, "wizard-small.bmp"), (2, "wizard-small@2x.bmp")):
        side = 55 * scale
        m = app_icon(43 * scale)
        on_background(WHITE, m, (side, side), ((side - m.width) // 2, (side - m.height) // 2)).save(ASSETS / name)
    print("icon and installer pictures written")


if __name__ == "__main__":
    main()
