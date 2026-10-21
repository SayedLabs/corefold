"""Draws Satsuma's icon (assets/icon.png and assets/icon.ico). Run: python scripts/make_icon.py"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024
ASSETS = Path(__file__).resolve().parent.parent / "assets"


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def fruit(arrows: bool) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cx, cy, r = 512, 560, 410

    # soft shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((cx - r + 20, cy - r + 40, cx + r + 20, cy + r + 40), fill=(0, 0, 0, 90))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(28)))

    # orange body: radial gradient lit from top-left
    body = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    light, mid, dark = (255, 196, 92), (255, 140, 26), (226, 84, 0)
    hx, hy = cx - 140, cy - 160
    for i in range(r, 0, -2):
        t = i / r
        col = lerp(mid, dark, (t - 0.55) / 0.45) if t > 0.55 else lerp(light, mid, t / 0.55)
        ox, oy = cx + (hx - cx) * (1 - t), cy + (hy - cy) * (1 - t)
        d.ellipse((ox - i, oy - i, ox + i, oy + i), fill=col + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    body.putalpha(mask)
    img.alpha_composite(body)

    # stem + leaf
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((496, 108, 528, 190), radius=14, fill=(96, 66, 32, 255))
    leaf = Image.new("RGBA", (300, 130), (0, 0, 0, 0))
    ld = ImageDraw.Draw(leaf)
    ld.ellipse((0, 0, 299, 129), fill=(70, 172, 78, 255))
    ld.line((24, 65, 276, 65), fill=(44, 124, 54, 255), width=9)
    leaf = leaf.rotate(24, resample=Image.BICUBIC, expand=True)
    img.alpha_composite(leaf, (512, 18))

    if arrows:  # two curved arrows = "convert"
        d = ImageDraw.Draw(img)
        white = (255, 255, 255, 245)
        ar, w = 205, 68
        box = (cx - ar, cy - ar, cx + ar, cy + ar)
        for start, end in ((200, 330), (20, 150)):
            d.arc(box, start, end, fill=white, width=w)
            a = math.radians(end)
            px, py = cx + (ar - w / 2) * math.cos(a), cy + (ar - w / 2) * math.sin(a)
            tx, ty = -math.sin(a), math.cos(a)        # clockwise tangent
            nx, ny = math.cos(a), math.sin(a)         # outward normal
            L, Wd = 110, 95
            d.polygon([(px + tx * L, py + ty * L),
                       (px + nx * Wd, py + ny * Wd),
                       (px - nx * Wd, py - ny * Wd)], fill=white)
    return img


def main():
    ASSETS.mkdir(exist_ok=True)
    big, simple = fruit(True), fruit(False)
    big.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon.png")
    sizes = [256, 128, 64, 48, 32, 24, 16]
    frames = [(simple if s <= 24 else big).resize((s, s), Image.LANCZOS) for s in sizes]
    frames[0].save(ASSETS / "icon.ico", sizes=[(s, s) for s in sizes], append_images=frames[1:])
    make_wizard_images(big)
    print("wrote icon.png, icon.ico and installer images in", ASSETS)


def make_wizard_images(icon: Image.Image):
    """Pictures for the installer: a tall side banner and a small corner logo (BMP, 1x and 2x)."""
    top, bottom = (255, 168, 64), (232, 92, 0)
    for scale in (1, 2):
        w, h = 164 * scale, 314 * scale
        banner = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(banner)
        for y in range(h):
            d.line((0, y, w, y), fill=lerp(top, bottom, y / h))
        # soft white circle behind the icon
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gs = int(w * 0.86)
        ImageDraw.Draw(glow).ellipse(((w - gs) // 2, int(h * 0.3) - gs // 2 + gs // 2 - gs // 2,
                                      (w + gs) // 2, int(h * 0.3) + gs // 2), fill=(255, 255, 255, 60))
        banner = banner.convert("RGBA")
        banner.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6 * scale)))
        size = int(w * 0.74)
        ic = icon.resize((size, size), Image.LANCZOS)
        banner.alpha_composite(ic, ((w - size) // 2, int(h * 0.3) - size // 2))
        suffix = "" if scale == 1 else "@2x"
        banner.convert("RGB").save(ASSETS / f"wizard{suffix}.bmp")

        s = 55 * scale
        small = Image.new("RGBA", (s, s), (255, 255, 255, 255))
        ic = icon.resize((s - 4 * scale, s - 4 * scale), Image.LANCZOS)
        small.alpha_composite(ic, (2 * scale, 2 * scale))
        small.convert("RGB").save(ASSETS / f"wizard-small{suffix}.bmp")


if __name__ == "__main__":
    main()
