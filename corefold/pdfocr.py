"""Corefold PDF's "Make searchable": scanned pages get their words as hidden text, so they can be searched,
selected and copied, and the pages look exactly as before.

Each page that has no text of its own is drawn as a picture (PDFium) and read with Windows' own OCR, the
same reader as Copy text (Arabic too, once Windows has its Arabic reading). Every word is written as
invisible text (render mode 3) over the place it is in the picture, stretched to the word's width, so
selecting with the mouse lands on the words. The words are put on a page of their own that is laid over
the original page with pypdf: nothing that was already in the PDF is drawn again or changed.

The hidden text uses a font made here for the file: one empty glyph for each letter used, a few KB,
instead of a whole Windows font (about a megabyte) in every PDF. Arabic words are written the way Word
and other PDF makers write them (letters in the order they're seen), so PDF apps read them back right."""
from __future__ import annotations

import ctypes
import io
import logging
import math
import struct
from pathlib import Path

from .core import Ctx, Result, Tool
from .i18n import tr
from .pdftools import PDFIUM, open_pdfium, open_reader, write

log = logging.getLogger("corefold")

DPI = 300               # pages are read at this sharpness (Windows reads print-size text best around here)
MAX_SIDE = 5000         # but never bigger than this many pixels on the longest side (an A3 page at 300 dpi)
FEW_CHARS = 40          # a scan with only this much real text (a page number stamped on it) is still read
BIG_PICTURE = 0.5       # ... if a picture covers at least this much of the page
_ADVANCE = 500          # every glyph of the hidden font is half an em wide (the words are stretched to fit)
_TEXT, _PATH, _IMAGE, _FORM = 1, 2, 3, 5
_HIDDEN_MODES = (3, 7)  # invisible, and clip only: text you can't see


# ----------------------------------------------------------------------------- what's on a page

def page_kind(page) -> tuple[str, list[tuple[float, float, float, float]]]:
    """("text" | "hidden" | "scan" | "empty", boxes of its visible text in page points).
    "hidden": only invisible text (made searchable before), "scan": pictures or drawings and no text to
    speak of. (a pypdfium2 page; call with PDFIUM held)"""
    import pypdfium2.raw as c
    w, h = page.get_size()
    area = max(1.0, w * h)
    visible, boxes = [], []
    hidden = 0
    pictures = drawn = False
    biggest = 0.0
    for kind, obj, box in _objects(page.raw):
        if kind == _TEXT:
            if c.FPDFTextObj_GetTextRenderMode(obj) in _HIDDEN_MODES:
                hidden += 1
            else:
                visible.append(obj)
                boxes.append(box)
        elif kind == _IMAGE:
            pictures = True
            l, b, r, t = box
            biggest = max(biggest, max(0.0, r - l) * max(0.0, t - b) / area)
        elif kind == _PATH:
            drawn = True
    chars = _text_length(page, visible) if visible else 0
    if chars >= FEW_CHARS or (chars and biggest < BIG_PICTURE):
        return "text", boxes
    if pictures or drawn:
        return ("hidden" if hidden and not chars else "scan"), boxes
    return ("hidden" if hidden else "empty"), boxes


def _objects(raw, depth: int = 0, box=None):
    """Every object on a page, inside groups (form XObjects) too: (type, object, its box on the page). An
    object inside a group gets the group's box (good enough to tell how much of the page it covers)."""
    import pypdfium2.raw as c
    form = depth > 0
    count = c.FPDFFormObj_CountObjects(raw) if form else c.FPDFPage_CountObjects(raw)
    for i in range(max(0, count)):
        obj = c.FPDFFormObj_GetObject(raw, i) if form else c.FPDFPage_GetObject(raw, i)
        if not obj:
            continue
        kind = c.FPDFPageObj_GetType(obj)
        here = box if box is not None else _bounds(obj)
        if kind == _FORM:
            if depth < 8:
                yield from _objects(obj, depth + 1, here)
            continue
        yield kind, obj, here


def _text_length(page, objs: list, enough: int = FEW_CHARS) -> int:
    """How many letters these text objects hold (counting stops at `enough`)."""
    import pypdfium2.raw as c
    tp = c.FPDFText_LoadPage(page.raw)
    try:
        n = 0
        for obj in objs:
            n += max(0, c.FPDFTextObj_GetText(obj, tp, None, 0) // 2 - 1)  # (UTF-16 bytes with the ending zero)
            if n >= enough:
                break
        return n
    finally:
        if tp:
            c.FPDFText_ClosePage(tp)


def _bounds(obj) -> tuple[float, float, float, float]:
    import pypdfium2.raw as c
    l, b, r, t = (ctypes.c_float() for _ in range(4))
    if c.FPDFPageObj_GetBounds(obj, l, b, r, t):
        return (l.value, b.value, r.value, t.value)
    return (0.0, 0.0, 0.0, 0.0)


def _overlaps(box, boxes) -> bool:
    l, b, r, t = box
    return any(min(r, R) - max(l, L) > 0 and min(t, T) - max(b, B) > 0 for L, B, R, T in boxes)


def _remove_hidden_text(page) -> int:
    """Takes out invisible text (an old searchable layer) before the page is read again."""
    import pypdfium2.raw as c
    gone = 0
    for i in reversed(range(c.FPDFPage_CountObjects(page.raw))):
        obj = c.FPDFPage_GetObject(page.raw, i)
        if c.FPDFPageObj_GetType(obj) == _TEXT and c.FPDFTextObj_GetTextRenderMode(obj) in _HIDDEN_MODES:
            if c.FPDFPage_RemoveObject(page.raw, obj):
                c.FPDFPageObj_Destroy(obj)
                gone += 1
    if gone:
        c.FPDFPage_GenerateContent(page.raw)
    return gone


# ----------------------------------------------------------------------------- the hidden font

def hidden_font(chars: str) -> bytes:
    """A TrueType font with an empty glyph (_ADVANCE wide) for each of these characters: PDF apps then know
    the letters (for search and copy) and draw nothing. (Characters past U+FFFF are left out.)"""
    codes = sorted({ord(ch) for ch in chars if 0x20 <= ord(ch) < 0xFFFF})
    n = len(codes) + 1  # (glyph 0 is the "missing" one)

    def search_fields(count: int, size: int) -> tuple[int, int, int]:
        power = 2 ** int(math.log2(max(1, count)))
        return power * size, int(math.log2(power)), (count - power) * size

    segs = [(cp, cp, (k + 1 - cp) & 0xFFFF) for k, cp in enumerate(codes)] + [(0xFFFF, 0xFFFF, 1)]
    rng, sel, shift = search_fields(len(segs), 2)
    sub = struct.pack(">HHH", 2 * len(segs), rng, sel) + struct.pack(">H", shift)
    sub += b"".join(struct.pack(">H", end) for _, end, _ in segs) + b"\0\0"
    sub += b"".join(struct.pack(">H", start) for start, _, _ in segs)
    sub += b"".join(struct.pack(">H", delta) for _, _, delta in segs)
    sub += b"\0\0" * len(segs)
    sub = struct.pack(">HHH", 4, 6 + len(sub), 0) + sub
    cmap = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + sub
    head = struct.pack(">IIIIHHqqhhhhHHhhh", 0x10000, 0x10000, 0, 0x5F0F3CF5, 0b1011, 1000, 0, 0,
                       0, -200, _ADVANCE, 800, 0, 8, 2, 0, 0)
    hhea = struct.pack(">IhhhHhhhhhhhhhhhH", 0x10000, 800, -200, 0, _ADVANCE, 0, 0, _ADVANCE, 1, 0, 0,
                       0, 0, 0, 0, 0, n)
    maxp = struct.pack(">IHHHHHHHHHHHHHH", 0x10000, n, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0)
    hmtx = struct.pack(">" + "Hh" * n, *([_ADVANCE, 0] * n))
    # every letter is the same box (never drawn: the text is invisible), so PDF apps select whole words
    box = struct.pack(">hhhhhHH4B4h4h", 1, 0, -200, _ADVANCE, 800, 3, 0, 1, 1, 1, 1,
                      0, _ADVANCE, 0, -_ADVANCE, -200, 0, 1000, 0)
    loca = struct.pack(">" + "H" * (n + 1), 0, *(k * len(box) // 2 for k in range(n)))  # (glyph 0 is empty)
    family = "Corefold Text".encode("utf-16-be")
    records = [(3, 1, 0x409, name_id, len(family), 0) for name_id in (1, 2, 4, 6)]  # (all point at one string)
    name = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records))
    name += b"".join(struct.pack(">HHHHHH", *r) for r in records) + family
    post = struct.pack(">IIhhIIIII", 0x30000, 0, 0, 0, 0, 0, 0, 0, 0)
    first, last = (codes[0], codes[-1]) if codes else (0x20, 0x20)
    os2 = struct.pack(">HhHHHhhhhhhhhhhh10sIIII4sHHHhhhHHII", 1, _ADVANCE, 400, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                      0, 0, 0, b"\0" * 10, 0, 0, 0, 0, b"CFLD", 0x40, first, last, 800, -200, 0, 800, 200, 1, 0)
    tables = {b"OS/2": os2, b"cmap": cmap, b"glyf": box * (n - 1), b"head": head, b"hhea": hhea, b"hmtx": hmtx,
              b"loca": loca, b"maxp": maxp, b"name": name, b"post": post}
    rng, sel, shift = search_fields(len(tables), 16)
    out = struct.pack(">IHHHH", 0x10000, len(tables), rng, sel, shift)
    at = 12 + 16 * len(tables)
    entries, data = b"", b""
    for tag in sorted(tables):
        body = tables[tag] + b"\0" * (-len(tables[tag]) % 4)
        check = sum(struct.unpack(f">{len(body) // 4}I", body)) & 0xFFFFFFFF
        entries += struct.pack(">4sIII", tag, check, at + len(data), len(tables[tag]))
        data += body
    return out + entries + data


# ----------------------------------------------------------------------------- placing the words

def _mul(m1, m2):
    """m1, then m2 (PDF matrices: x' = a x + c y + e, y' = b x + d y + f)."""
    a, b, c, d, e, f = m1
    A, B, C, D, E, F = m2
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _rtl(text: str) -> bool:
    import unicodedata
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text)


def seen_order(text: str) -> str:
    """A word's letters in the order they're seen from left to right (how PDF makers write Arabic)."""
    return text[::-1] if _rtl(text) else text


def line_pieces(line) -> list[tuple[str, float, float, float, float]]:
    """A line Windows read (extras.OcrLine), as the pieces to write from left to right: words and the
    spaces between them, (text, x, y, w, h) in the picture's pixels, as if the line were level."""
    words = sorted((w for w in line.words if w.text.strip()), key=lambda w: w.x)
    out = []
    for k, w in enumerate(words):
        if k:
            prev = words[k - 1]
            gap_x = prev.x + prev.w
            gap_w = w.x - gap_x
            if gap_w < 0.05 * w.h:  # (touching or overlapping: a thin space at the join)
                gap_x, gap_w = min(gap_x, w.x) - 0.05 * w.h, 0.1 * w.h
            top = min(prev.y, w.y)
            out.append((" ", gap_x, top, gap_w, max(prev.y + prev.h, w.y + w.h) - top))
        out.append((seen_order(w.text.strip()), w.x, w.y, w.w, w.h))
    return out


def piece_matrix(text: str, x: float, y: float, w: float, h: float, line, scale: float, page_h: float):
    """Where a piece's text goes: from the font's own units to the page as shown (points, y up). The em box
    spans the word's height (ascent 0.8, descent 0.2) and its letters are stretched to the word's width."""
    sx = w / max(1, len(text)) / (_ADVANCE / 1000)
    m = (sx, 0.0, 0.0, -h, x, y + 0.8 * h)  # font units (y up) -> picture pixels (y down), baseline at 0.8 h
    angle = getattr(line, "angle", 0.0) or 0.0
    if abs(angle) > 0.05:  # turned text: around the point Windows turned it around
        r = math.radians(angle)
        cos, sin = math.cos(r), math.sin(r)
        cx, cy = line.cx, line.cy
        m = _mul(m, (cos, sin, -sin, cos, cx - cx * cos + cy * sin, cy - cx * sin - cy * cos))
    return _mul(m, (1 / scale, 0.0, 0.0, -1 / scale, 0.0, page_h))  # pixels -> points, y up


def _wide(text: str):
    raw = (text + "\0").encode("utf-16-le")
    return (ctypes.c_ushort * (len(raw) // 2)).from_buffer_copy(raw)


def text_layer(pages: list[tuple[float, float, list]], chars: str) -> bytes:
    """A PDF of pages (width, height, pieces: (text, matrix)) holding only invisible text."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    font_data = hidden_font(chars)
    buf = (ctypes.c_uint8 * len(font_data)).from_buffer_copy(font_data)
    with PDFIUM:
        doc = pdfium.PdfDocument.new()
        font = None
        try:
            font = c.FPDFText_LoadFont(doc.raw, buf, len(font_data), c.FPDF_FONT_TRUETYPE, True)
            if not font:
                raise RuntimeError(tr("couldn't make the hidden text"))
            for width, height, pieces in pages:
                page = doc.new_page(width, height)
                try:
                    for text, m in pieces:
                        obj = c.FPDFPageObj_CreateTextObj(doc.raw, font, 1.0)
                        if not obj:
                            continue
                        if not c.FPDFText_SetText(obj, _wide(text)):
                            c.FPDFPageObj_Destroy(obj)
                            continue
                        c.FPDFTextObj_SetTextRenderMode(obj, c.FPDF_TEXTRENDERMODE_INVISIBLE)
                        c.FPDFPageObj_Transform(obj, *m)
                        c.FPDFPage_InsertObject(page.raw, obj)
                    if not c.FPDFPage_GenerateContent(page.raw):
                        raise RuntimeError(tr("couldn't make the hidden text"))
                finally:
                    page.close()
            out = io.BytesIO()
            doc.save(out)
            return out.getvalue()
        finally:
            if font:
                c.FPDFFont_Close(font)
            doc.close()


# ----------------------------------------------------------------------------- the tool

def _read(im):
    """(where tests stand in for Windows' reader)"""
    from .extras import read_lines
    return read_lines(im, enlarge=False)


def make_searchable(src: Path, ctx: Ctx) -> Result:
    """ctx.params: password, redo (read pages made searchable before again, their old hidden text taken out)."""
    from pypdf import PdfReader, PdfWriter, Transformation
    from .pdfedit import page_geo
    password = ctx.params.get("password", "") or ""
    redo = bool(ctx.params.get("redo"))
    open_reader(src, password)  # (a wrong password stops here, before any reading)
    read: dict[int, tuple[float, float, list]] = {}  # page -> (width, height as shown, pieces)
    kinds: dict[str, int] = {}
    chars: set[str] = set()
    redone = False
    with PDFIUM:
        doc = open_pdfium(src, password)
    try:
        with PDFIUM:
            count = len(doc)
        for i in range(count):
            ctx.check()
            with PDFIUM:
                page = doc[i]
                try:
                    kind, text_boxes = page_kind(page)
                    if kind == "hidden" and redo:
                        redone |= _remove_hidden_text(page) > 0
                        kind = "scan"
                    kinds[kind] = kinds.get(kind, 0) + 1
                    if kind != "scan":
                        continue
                    geo = page_geo(page)
                    scale = min(DPI / 72, MAX_SIDE / max(1.0, geo.width, geo.height))
                    im = page.render(scale=scale, may_draw_forms=True).to_pil()
                finally:
                    page.close()
            try:
                lines = _read(im)  # (PDFium is free meanwhile: the window can draw its pages)
            finally:
                im.close()
            pieces = []
            for line in lines:
                for text, x, y, w, h in line_pieces(line):
                    if text_boxes and text != " " and _overlaps(
                            geo.rect_to_user(x / scale, y / scale, w / scale, h / scale), text_boxes):
                        continue  # (its own text is already there: a page number stamped on the scan)
                    pieces.append((text, piece_matrix(text, x, y, w, h, line, scale, geo.height)))
                    chars.update(text)
            if any(t != " " for t, _ in pieces):
                read[i] = (geo.width, geo.height, pieces)
            ctx.progress(0.85 * (i + 1) / max(1, count))
        if redone:  # (the old hidden text has to go from the copy too)
            with PDFIUM:
                buf = io.BytesIO()
                doc.save(buf)
            base = PdfReader(io.BytesIO(buf.getvalue()))
            if base.is_encrypted:
                base.decrypt(password)
        else:
            base = open_reader(src, password)
    finally:
        with PDFIUM:
            doc.close()
    if not read:
        if kinds.get("scan"):
            return Result(note=_no_words_note(src))
        if kinds.get("hidden"):
            return Result(note=tr("{name} can already be searched", name=src.name))
        return Result(note=tr("{name} already has its own text: it can be searched as it is", name=src.name))
    ctx.check()
    order = sorted(read)
    layer = PdfReader(io.BytesIO(text_layer([read[i] for i in order], "".join(chars))))
    writer = PdfWriter(clone_from=base)
    for n, i in enumerate(order):
        target = writer.pages[i]
        geo = _pypdf_geo(target)
        target.merge_transformed_page(layer.pages[n], Transformation(geo.ctm()))
        ctx.progress(0.85 + 0.1 * (n + 1) / len(order))
    out = ctx.out_path(src, f"{ctx.stem(src)} (searchable).pdf")
    write(writer, out, password)
    note = tr("Made {count} pages searchable", count=len(order)) if len(order) != 1 else \
        tr("Made 1 page searchable")
    missing = _arabic_missing()
    if missing:
        note += " · " + missing
    return Result([out], note=note)


def _pypdf_geo(page):
    from .pdfedit import pypdf_geo
    return pypdf_geo(page)


def _arabic_missing() -> str:
    try:
        from .extras import rtl_reader_missing, writes_arabic
        if writes_arabic() and rtl_reader_missing():
            return tr("Windows can't read Arabic yet: add it in Settings → Time & language → Language")
    except Exception:
        log.debug("couldn't ask Windows about Arabic", exc_info=True)
    return ""


def _no_words_note(src: Path) -> str:
    note = tr("No text found in {name}", name=src.name)
    missing = _arabic_missing()
    return note + (" · " + missing if missing else "")


def check(files: list[Path]) -> str | None:
    from .extras import check_ocr
    return check_ocr(files)


SEARCHABLE = Tool("pdftool:searchable", tr("Make searchable"), tr("Make a scanned PDF searchable"), make_searchable,
                  check=check, password=True, renames=False)
