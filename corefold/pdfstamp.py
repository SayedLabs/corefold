"""Corefold PDF: page numbers and a watermark on every page.

Both are drawn on top of each page with pypdf (the pages themselves aren't changed or made
blurry): the numbers as real text, the watermark as one see-through picture made by the same code
as the pictures' and videos' watermark (text, logo, place, size and how see-through)."""
from __future__ import annotations

import zlib
from pathlib import Path

from .core import Ctx, Result
from .i18n import tr

NUMBER_PLACES = {"bc": "Bottom middle", "br": "Bottom right", "bl": "Bottom left", "tc": "Top middle",
                 "tr": "Top right"}
WM_DPI = 110          # the watermark picture's sharpness on the page
WM_MAX_PX = 2400      # (a poster-sized page isn't given a huge picture)
# Helvetica's widths (in 1/1000 of the font size) of what a page number is made of
_WIDTHS = {**dict.fromkeys("0123456789", 556), " ": 278, "/": 278}


def _reader(src: Path, ctx: Ctx):
    from pypdf import PdfReader
    reader = PdfReader(str(src))
    if reader.is_encrypted and not reader.decrypt(ctx.params.get("password", "") or ""):
        raise RuntimeError(tr("this PDF is password-protected"))
    return reader


def _upright(page):
    """The page's box as it's shown (its CropBox: a cut-off edge isn't shown), with a turned page's turn
    moved into its drawing (so "bottom" is the bottom)."""
    if page.rotation % 360:
        page.transfer_rotation_to_content()
    box = page.cropbox
    return float(box.left), float(box.bottom), float(box.width), float(box.height)


def _stamp(page, writer, content: bytes, resources: dict):
    """Draws content (PDF drawing commands) on top of the page."""
    from pypdf import PageObject
    from pypdf.generic import ContentStream, DictionaryObject, NameObject
    x0, y0, w, h = float(page.cropbox.left), float(page.cropbox.bottom), float(page.cropbox.width), \
        float(page.cropbox.height)
    over = PageObject.create_blank_page(writer, width=w, height=h)
    over.mediabox.lower_left = (x0, y0)
    over.mediabox.upper_right = (x0 + w, y0 + h)
    res = DictionaryObject()
    for key, value in resources.items():
        res[NameObject(key)] = value
    over[NameObject("/Resources")] = res
    stream = ContentStream(None, writer)
    stream.set_data(content)
    over.replace_contents(stream)
    page.merge_page(over)


def _save(writer, src: Path, ctx: Ctx, suffix: str) -> Path:
    out = ctx.out_path(src, f"{ctx.stem(src)} ({suffix}).pdf")
    with open(out, "wb") as fh:
        writer.write(fh)
    return out


# ----------------------------------------------------------------------------- page numbers

def number_text(n: int, total: int, style: str) -> str:
    return f"{n} / {total}" if style == "of" else str(n)


def _text_width(text: str, size: float) -> float:
    return sum(_WIDTHS.get(c, 556) for c in text) * size / 1000


def page_numbers(src: Path, ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject
    place = ctx.params.get("place", "bc")
    if place not in NUMBER_PLACES:
        place = "bc"
    style = ctx.params.get("style", "n")
    first = max(0, int(ctx.params.get("skip", 0) or 0))  # pages left without a number (a cover)
    start = max(1, int(ctx.params.get("start", 1) or 1))  # the first page's number
    writer = PdfWriter(clone_from=_reader(src, ctx))
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    pages = writer.pages
    total = len(pages) - first
    last = total + start - 1  # ("12 / 20" when a chapter of pages 12 to 20 starts at 12)
    for i, page in enumerate(pages):
        ctx.check()
        if i < first:
            continue
        x0, y0, w, h = _upright(page)
        size = max(7.0, min(14.0, min(w, h) / 55))
        text = number_text(i - first + start, last, style)
        tw = _text_width(text, size)
        margin = max(18.0, min(w, h) * 0.04)
        x = {"l": margin, "c": (w - tw) / 2, "r": w - margin - tw}[place[1]]
        y = margin if place[0] == "b" else h - margin - size * 0.75
        # dark grey numbers on a soft white edge, readable on white pages and on photos
        content = (f"q 1 1 1 RG 1 1 1 rg {size * 0.18:.2f} w 1 j 2 Tr BT /CfNum {size:.2f} Tf "
                   f"{x0 + x:.2f} {y0 + y:.2f} Td ({text}) Tj ET Q "
                   f"q 0.2 0.2 0.2 rg BT /CfNum {size:.2f} Tf {x0 + x:.2f} {y0 + y:.2f} Td ({text}) Tj ET Q")
        _stamp(page, writer, content.encode("ascii"), {"/Font": DictionaryObject({NameObject("/CfNum"): font})})
        ctx.progress((i + 1) / len(pages))
    return Result([_save(writer, src, ctx, "numbered")], note=tr("{count} pages numbered", count=max(0, total)))


# ----------------------------------------------------------------------------- watermark

def _picture(writer, im):
    """A see-through Pillow picture as a PDF picture (its colours, and its see-through part as a mask)."""
    from pypdf.generic import NameObject, NumberObject, StreamObject

    def stream(data: bytes, space: str, **extra):
        s = StreamObject()
        s._data = zlib.compress(data, 6)
        s.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(im.width), NameObject("/Height"): NumberObject(im.height),
                  NameObject("/ColorSpace"): NameObject(space), NameObject("/BitsPerComponent"): NumberObject(8),
                  NameObject("/Filter"): NameObject("/FlateDecode"), **extra})
        return writer._add_object(s)
    mask = stream(im.getchannel("A").tobytes(), "/DeviceGray")
    return stream(im.convert("RGB").tobytes(), "/DeviceRGB", **{NameObject("/SMask"): mask})


def watermark_pdf(src: Path, ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject

    from .extras import make_overlay
    writer = PdfWriter(clone_from=_reader(src, ctx))
    pages = writer.pages
    made: dict = {}  # (width, height) -> (picture, its box on the page): pages of one size share it
    for i, page in enumerate(pages):
        ctx.check()
        x0, y0, w, h = _upright(page)
        key = (round(w), round(h))
        if key not in made:
            scale = min(WM_DPI / 72, WM_MAX_PX / max(w, h, 1))
            pw, ph = max(16, round(w * scale)), max(16, round(h * scale))
            overlay = make_overlay(pw, ph, {**ctx.params, "dark": True})
            box = overlay.getbbox()  # only the part with something on it
            if box is None:
                made[key] = None
            else:
                part = overlay.crop(box)
                l, t, r, b = box
                made[key] = (_picture(writer, part), l / pw * w, (ph - b) / ph * h, (r - l) / pw * w, (b - t) / ph * h)
        if made[key] is None:
            continue
        pic, bx, by, bw, bh = made[key]
        content = f"q {bw:.3f} 0 0 {bh:.3f} {x0 + bx:.3f} {y0 + by:.3f} cm /CfMark Do Q"
        _stamp(page, writer, content.encode("ascii"), {"/XObject": DictionaryObject({NameObject("/CfMark"): pic})})
        ctx.progress((i + 1) / len(pages))
    return Result([_save(writer, src, ctx, "watermarked")])


# ----------------------------------------------------------------------------- signature

SIGN_PAGES = {"last": "The last page", "first": "The first page", "all": "Every page"}
SIGN_PLACES = {"br": "Bottom right", "bl": "Bottom left", "bc": "Bottom middle"}
SIGN_SIZES = {"small": 0.18, "medium": 0.26, "large": 0.36}  # of the page's width


def signature_picture(path: Path):
    """The signature, cut to its ink: a photo or scan of one on white paper loses the paper."""
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
    if im.getchannel("A").getextrema()[0] == 255:  # no see-through parts: make the paper see-through
        grey = ImageOps.autocontrast(im.convert("L"), cutoff=1)
        ink = grey.point(lambda v: 0 if v > 200 else 255 if v < 120 else int((200 - v) * 255 / 80))
        im.putalpha(ink)
    box = im.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    if box is None:
        raise RuntimeError(tr("the signature is empty"))
    return im.crop(box)


def sign_pdf(src: Path, ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject
    sig_path = Path(ctx.params.get("signature") or "")
    if not sig_path.is_file():
        raise RuntimeError(tr("draw your signature or pick a picture of it"))
    sig = signature_picture(sig_path)
    if max(sig.size) > 1600:
        sig.thumbnail((1600, 1600))
    which = ctx.params.get("pages", "last")
    place = ctx.params.get("place", "br")
    rel = SIGN_SIZES.get(ctx.params.get("size", "medium"), 0.26)
    writer = PdfWriter(clone_from=_reader(src, ctx))
    pages = writer.pages
    picked = range(len(pages)) if which == "all" else [0] if which == "first" else [len(pages) - 1]
    pic = _picture(writer, sig)
    for n, i in enumerate(picked):
        ctx.check()
        page = pages[i]
        x0, y0, w, h = _upright(page)
        bw = w * rel
        bh = bw * sig.height / sig.width
        if bh > h * 0.25:  # a tall signature isn't allowed to cover the page
            bh = h * 0.25
            bw = bh * sig.width / sig.height
        margin = max(24.0, min(w, h) * 0.06)
        x = {"bl": margin, "bc": (w - bw) / 2}.get(place, w - margin - bw)
        content = f"q {bw:.3f} 0 0 {bh:.3f} {x0 + x:.3f} {y0 + margin:.3f} cm /CfSign Do Q"
        _stamp(page, writer, content.encode("ascii"), {"/XObject": DictionaryObject({NameObject("/CfSign"): pic})})
        ctx.progress((n + 1) / len(picked))
    return Result([_save(writer, src, ctx, "signed")])
