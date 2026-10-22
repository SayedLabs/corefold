"""Draws Corefold's icon and installer pictures from the Corefold mark: a rounded square with its top
corner folded over and a round "core" cut out of it, in a teal gradient on a near-black tile.

Makes assets/icon.png, assets/icon.ico, docs/icon.png and the installer's wizard*.bmp.
Run: python scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
TOP = (107, 230, 210)      # #6BE6D2, the light end of the gradient
BOTTOM = (15, 143, 124)    # #0F8F7C, the deep end
TILE = (11, 11, 12)        # #0B0B0C
EDGE = (38, 40, 44)        # #26282C, the tile's fine border
WHITE = (255, 255, 255)
S = 1024                   # drawn big, then made smaller (smooth edges)
U = S / 64                 # the design is on a 64-unit grid


def _box(x0, y0, x1, y1):
    return (x0 * U, y0 * U, x1 * U, y1 * U)


def gradient(size: int, top=TOP, bottom=BOTTOM) -> Image.Image:
    """Top-left to bottom-right."""
    down = Image.linear_gradient("L").resize((size, size), Image.BILINEAR)
    ramp = Image.blend(down, down.rotate(90), 0.5)  # (x + y) / 2: corner to corner
    return Image.composite(Image.new("RGBA", (size, size), bottom + (255,)),
                           Image.new("RGBA", (size, size), top + (255,)), ramp)


def mark_masks() -> tuple[Image.Image, Image.Image]:
    """The body (with the notch and the core cut out) and the folded corner, as masks.
    Body: 10..54 with 10-unit corners; notch x > 33, y < 31; core at (31, 37) r 7.
    Fold: a triangle (37, 10) (56, 29) (37, 29) with softened corners."""
    body = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(body)
    d.rounded_rectangle(_box(10, 10, 54, 54), radius=10 * U, fill=255)
    d.rectangle(_box(33, 0, 64, 31), fill=0)
    d.ellipse(_box(24, 30, 38, 44), fill=0)
    fold = Image.new("L", (S, S), 0)
    ImageDraw.Draw(fold).polygon([(37 * U, 10.2 * U), (56 * U, 29.2 * U), (37 * U, 29.2 * U)], fill=255)
    fold = fold.filter(ImageFilter.GaussianBlur(0.9 * U)).point(lambda a: 255 if a > 110 else 0)  # soft corners
    return body, fold


def tile_mask() -> Image.Image:
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle(_box(2, 2, 62, 62), radius=15 * U, fill=255)
    return m


def _zoomed(mask: Image.Image, zoom: float) -> Image.Image:
    if zoom == 1:
        return mask
    big = mask.resize((round(S * zoom), round(S * zoom)), Image.BILINEAR)
    off = (big.width - S) // 2
    return big.crop((off, off, off + S, off + S))


def app_icon(size: int, tile: bool = True, zoom: float = 1.0) -> Image.Image:
    """The full icon: the gradient mark on the dark tile, with a soft teal glow behind it.
    zoom > 1 makes the mark bigger in its tile (for the tiny sizes, where detail gets lost)."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if tile:
        tm = tile_mask()
        img.paste(EDGE + (255,), (0, 0), tm)
        inner = Image.new("L", (S, S), 0)
        ImageDraw.Draw(inner).rounded_rectangle(_box(2.8, 2.8, 61.2, 61.2), radius=14.2 * U, fill=255)
        img.paste(TILE + (255,), (0, 0), inner)
        glow = Image.new("L", (S, S), 0)
        ImageDraw.Draw(glow).ellipse(_box(8, 4, 56, 48), fill=80)
        glow = glow.filter(ImageFilter.GaussianBlur(9 * U))
        glow = ImageChops.multiply(glow, inner)
        layer = Image.new("RGBA", (S, S), (60, 199, 178, 0))
        layer.putalpha(glow)
        img.alpha_composite(layer)
    body, fold = (_zoomed(m, zoom) for m in mark_masks())
    g = gradient(S)
    g.putalpha(body)
    img.alpha_composite(g)
    f = Image.new("RGBA", (S, S), TOP + (0,))
    f.putalpha(fold.point(lambda a: a * 0.62))
    img.alpha_composite(f)
    return img.resize((size, size), Image.LANCZOS)


def mark_only(size: int, color=None) -> Image.Image:
    """The mark without the tile (one colour, or the gradient when color is None)."""
    body, fold = mark_masks()
    img = gradient(S) if color is None else Image.new("RGBA", (S, S), color + (255,))
    img.putalpha(body)
    f = Image.new("RGBA", (S, S), (TOP if color is None else color) + (0,))
    f.putalpha(fold.point(lambda a: a * (0.62 if color is None else 0.55)))
    img.alpha_composite(f)
    return img.resize((size, size), Image.LANCZOS)


def on_background(bg, mark_img: Image.Image, size: tuple[int, int], at: tuple[int, int]) -> Image.Image:
    out = Image.new("RGBA", size, bg + (255,))
    out.alpha_composite(mark_img, at)
    return out.convert("RGB")


def wizard(scale: int) -> Image.Image:
    """The tall picture on the installer's first and last pages: the icon on near-black, with a
    quiet pattern of small marks and a teal glow."""
    w, h = 164 * scale, 314 * scale
    out = Image.new("RGBA", (w, h), (9, 9, 10, 255))
    small = mark_only(18 * scale, WHITE)
    small.putalpha(small.getchannel("A").point(lambda a: a * 0.05))
    step = 30 * scale
    for row, y in enumerate(range(10 * scale, h, step)):
        for x in range(10 * scale + (row % 2) * step // 2, w, step):
            out.alpha_composite(small, (x, y))
    glow = Image.new("L", (w, h), 0)
    cy = h // 2 - 20 * scale
    ImageDraw.Draw(glow).ellipse((w // 2 - 70 * scale, cy - 70 * scale, w // 2 + 70 * scale, cy + 70 * scale), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(28 * scale))
    layer = Image.new("RGBA", (w, h), (60, 199, 178, 0))
    layer.putalpha(glow)
    out.alpha_composite(layer)
    big = app_icon(112 * scale)
    out.alpha_composite(big, ((w - big.width) // 2, cy - big.height // 2))
    return out.convert("RGB")


def main():
    icon = app_icon(512)
    icon.save(ASSETS / "icon.png")
    icon.save(ROOT / "docs" / "icon.png")
    big = app_icon(256)
    small = [app_icon(n, zoom=1.2 if n <= 24 else 1.1 if n <= 40 else 1.0) for n in (16, 20, 24, 32, 40, 48, 64, 128)]
    big.save(ASSETS / "icon.ico", sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)],
             append_images=small)
    for scale, name in ((1, "wizard.bmp"), (2, "wizard@2x.bmp")):
        wizard(scale).save(ASSETS / name)
    for scale, name in ((1, "wizard-small.bmp"), (2, "wizard-small@2x.bmp")):
        side = 55 * scale
        m = app_icon(47 * scale)
        on_background(WHITE, m, (side, side), ((side - m.width) // 2, (side - m.height) // 2)).save(ASSETS / name)
    print("icon and installer pictures written")


if __name__ == "__main__":
    main()
