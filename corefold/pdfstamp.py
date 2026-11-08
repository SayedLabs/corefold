"""Rakiza Core PDF: page numbers and a watermark on every page.

Both are drawn on top of each page with pypdf (the pages themselves aren't changed or made
blurry): the numbers as real text, the watermark as one see-through picture made by the same code
as the pictures' and videos' watermark (text, logo, place, size and how see-through)."""
from __future__ import annotations

import zlib
from pathlib import Path

from .core import Ctx, Result, Tool
from .i18n import tr

NUMBER_PLACES = {"bc": "Bottom middle", "br": "Bottom right", "bl": "Bottom left", "tc": "Top middle",
                 "tr": "Top right"}
WM_DPI = 110          # the watermark picture's sharpness on the page
WM_MAX_PX = 2400      # (a poster-sized page isn't given a huge picture)
# Helvetica's widths (in 1/1000 of the font size) of what a page number is made of
_WIDTHS = {**dict.fromkeys("0123456789", 556), " ": 278, "/": 278}


def _reader(src: Path, ctx: Ctx):
    """The PDF, unlocked with its password (the app asks for it when it's missing or wrong)."""
    from .pdftools import open_reader
    return open_reader(src, ctx.params.get("password", "") or "")


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
    """A locked PDF's new copy is locked with the same password (it used to come out open)."""
    from .pdftools import write
    return write(writer, ctx.out_path(src, f"{ctx.stem(src)} ({suffix}).pdf"), ctx.params.get("password", "") or "")


# ----------------------------------------------------------------------------- page numbers

ARABIC_STYLES = ("ar", "ar_of")  # ١، ٢، ٣ (the digits Arabic is written with), drawn with a font Rakiza Core brings
_TO_ARABIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def number_text(n: int, total: int, style: str) -> str:
    text = f"{n} / {total}" if style in ("of", "ar_of") else str(n)
    return text.translate(_TO_ARABIC) if style in ARABIC_STYLES else text


def _arabic_numbers(pages: list[tuple[float, float, str, float, str]]):
    """The numbers in Arabic digits as a PDF of see-through pages, one per page numbered, (width, height, text,
    size, place) each. Drawn as shapes in Cairo (a font Rakiza Core brings, so it looks the same on any PC), dark
    grey on a soft white edge like the plain numbers. Returns the PDF's pages (pypdf)."""
    import io

    from pypdf import PdfReader
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QPointF, QSizeF, Qt
    from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter, QPainterPath,
                               QPdfWriter, QPen)

    from .pdffonts import ready
    ready()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    pdf = QPdfWriter(buf)
    pdf.setResolution(72)  # (one unit = one PDF point)

    def layout(w, h):
        return QPageLayout(QPageSize(QSizeF(w, h), QPageSize.Point), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0))
    pdf.setPageLayout(layout(pages[0][0], pages[0][1]))
    painter = QPainter(pdf)
    try:
        for i, (w, h, text, size, place) in enumerate(pages):
            if i:
                pdf.setPageLayout(layout(w, h))
                pdf.newPage()
            font = QFont("Cairo")
            font.setPointSizeF(size * 1.15)  # (Cairo's digits are a little smaller than Helvetica's)
            fm = QFontMetricsF(font)
            tw = fm.horizontalAdvance(text)
            margin = max(18.0, min(w, h) * 0.04)
            x = {"l": margin, "c": (w - tw) / 2, "r": w - margin - tw}[place[1]]
            base = h - margin if place[0] == "b" else margin + fm.ascent() * 0.8
            path = QPainterPath()
            path.addText(QPointF(x, base), font, text)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.strokePath(path, QPen(QColor(255, 255, 255), size * 0.18, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.fillPath(path, QColor(51, 51, 51))
    finally:
        painter.end()
    buf.close()
    return PdfReader(io.BytesIO(bytes(data))).pages


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
    if style in ARABIC_STYLES:
        return _number_in_arabic(writer, src, ctx, place, style, first, start, last, total)
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


def _number_in_arabic(writer, src: Path, ctx: Ctx, place: str, style: str, first: int, start: int, last: int,
                      total: int) -> Result:
    from pypdf import Transformation
    todo = []
    for i, page in enumerate(writer.pages):
        ctx.check()
        if i < first:
            continue
        x0, y0, w, h = _upright(page)
        size = max(7.0, min(14.0, min(w, h) / 55))
        todo.append((i, x0, y0, w, h, number_text(i - first + start, last, style), size))
    if todo:
        drawn = _arabic_numbers([(w, h, text, size, place) for _, _, _, w, h, text, size in todo])
        for n, (i, x0, y0, *_rest) in enumerate(todo):
            ctx.check()
            writer.pages[i].merge_transformed_page(drawn[n], Transformation().translate(x0, y0))
            ctx.progress((n + 1) / len(todo))
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


# ----------------------------------------------------------------------------- header and footer, and a background

HF_PLACES = {"l": "Left", "c": "Middle", "r": "Right"}
HF_FIELDS = (("{page}", "Page number"), ("{pages}", "Number of pages"), ("{date}", "Date"), ("{name}", "File name"))
BACKGROUNDS = {"": "None", "#fff8e1": "Soft yellow", "#f1f3f4": "Light grey", "#e8f0fe": "Light blue",
               "#e8f5e9": "Light green"}


def fill_fields(text: str, page: int, pages: int, date: str, name: str) -> str:
    return (text.replace("{page}", str(page)).replace("{pages}", str(pages)).replace("{date}", date)
            .replace("{name}", name))


def _background(src: Path, ctx: Ctx, color: str, out: Path):
    """A copy of the PDF with a colour behind every page (what's on the page is drawn over it)."""
    from pypdf import PageObject, PdfWriter
    from pypdf.generic import ContentStream
    from PySide6.QtGui import QColor
    c = QColor(color)
    writer = PdfWriter(clone_from=_reader(src, ctx))
    for n, page in enumerate(writer.pages):
        ctx.check()
        box = page.mediabox
        x0, y0, w, h = float(box.left), float(box.bottom), float(box.width), float(box.height)
        under = PageObject.create_blank_page(writer, width=w, height=h)
        stream = ContentStream(None, writer)
        stream.set_data(f"q {c.redF():.3f} {c.greenF():.3f} {c.blueF():.3f} rg {x0:.2f} {y0:.2f} {w:.2f} {h:.2f} re f Q"
                        .encode("ascii"))
        under.replace_contents(stream)
        page.merge_transformed_page(under, (1, 0, 0, 1, 0, 0), over=False)
        ctx.progress(0.3 * (n + 1) / len(writer.pages))
    with open(out, "wb") as f:
        writer.write(f)


def header_footer(src: Path, ctx: Ctx) -> Result:
    """ctx.params: header and footer (words, with {page}, {pages}, {date} and {name} filled in), their places
    (l, c or r), size, skip (pages at the start left without them: a cover), background (a colour, or "")."""
    import tempfile

    from PySide6.QtCore import QDate, QLocale
    from PySide6.QtGui import QFontMetricsF

    from . import i18n, pdfedit
    p = ctx.params
    header, footer = str(p.get("header", "") or "").strip(), str(p.get("footer", "") or "").strip()
    color = str(p.get("background", "") or "")
    if not header and not footer and not color:
        raise RuntimeError(tr("nothing was changed"))
    size = max(6.0, min(28.0, float(p.get("size", 10) or 10)))
    skip = max(0, int(p.get("skip", 0) or 0))
    places = {"h": p.get("header_place", "c"), "f": p.get("footer_place", "c")}
    date = QLocale(i18n.language()).toString(QDate.currentDate(), QLocale.FormatType.ShortFormat)
    name = ctx.stem(src)
    password = p.get("password", "") or ""
    work_src = src
    tmp = Path(tempfile.mkdtemp(prefix="corefold-hf-"))
    try:
        if color:
            work_src = tmp / "with background.pdf"
            _background(src, ctx, color, work_src)
        from .pdftools import open_reader
        reader = open_reader(work_src, password if work_src == src else "")
        total = len(reader.pages)
        style = {"font": "Arial", "size": size, "color": "#444444", "bold": False, "italic": False,
                 "underline": False, "align": "", "opacity": 1.0}
        fm = QFontMetricsF(pdfedit.font_for(style), pdfedit.layout_device())
        edits = []
        for i, page in enumerate(reader.pages):
            if i < skip:
                continue
            geo = pdfedit.pypdf_geo(page)
            w, h = geo.width, geo.height
            margin = max(18.0, min(w, h) * 0.045)
            for where, words in (("h", header), ("f", footer)):
                if not words:
                    continue
                text = fill_fields(words, i - skip + 1, total - skip, date, name)
                tw = fm.horizontalAdvance(text)
                x = {"l": margin, "r": w - margin - tw}.get(places[where], (w - tw) / 2)
                y = margin * 0.7 if where == "h" else h - margin * 0.7 - fm.height()
                edits.append(dict(style, kind="text", page=i, text=text, x=round(max(0.0, x), 2),
                                  y=round(max(0.0, y), 2)))
        if not edits:  # (only a background)
            out = ctx.out_path(src, f"{ctx.stem(src)} ({tr('header and footer')}).pdf")
            from .pdftools import write
            from pypdf import PdfWriter
            write(PdfWriter(clone_from=reader), out, password)
            return Result([out])
        import copy
        options = copy.copy(ctx.options)
        options.output_folder = tmp  # (made there first: a half-made file never lands next to the PDF)
        inner = Ctx(options, lambda f: ctx.progress(0.3 + 0.7 * f), ctx.cancel_event,
                     {"edits": edits, "password": password})
        made = pdfedit.save_edits(work_src, inner).outputs[0]
        out = ctx.out_path(src, f"{ctx.stem(src)} ({tr('header and footer')}).pdf")
        import shutil
        shutil.move(str(made), str(out))  # (the folders may be on two drives)
        return Result([out])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


HEADER_FOOTER = Tool("pdftool:headerfooter", tr("Header and footer"), tr("Words at the top and bottom of every page"),
                     header_footer, password=True, renames=False)
