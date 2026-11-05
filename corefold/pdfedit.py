"""Rakiza Core PDF's page editor, the part with no window: reading what is on a page (its lines of text,
pictures and form boxes) and writing the edited copy.

What is added (text, pictures, a signature, drawings, shapes, covers, underlines) is drawn by Qt into a
small PDF of its own, as real text in an embedded font (so Arabic is joined and right to left, and the
words can be selected and searched), then laid on top of the page with pypdf. Highlights are written
straight into the page ("multiply", like a highlighter pen: the words under them stay black). Text and
pictures that were already in the PDF and are changed, moved or deleted are taken out of the page itself
with PDFium, so the old words are really gone and not just hidden. Notes become the PDF's own sticky
notes; form boxes get their new values and a picture of them that every PDF app shows the same way.

Positions are on the page as it is shown (turned pages included), in points from its top left corner."""
from __future__ import annotations

import ctypes
import io
import logging
import math
import re
import unicodedata
from pathlib import Path

from .core import Ctx, Result, Tool
from .i18n import tr
from .pdftools import PDFIUM, open_reader, write

log = logging.getLogger("corefold")

HIGHLIGHT_BLEND = 0.9   # a highlight's strength on the page (multiply, so text under it stays dark)
PICTURE_DPI = 220       # added pictures are kept this sharp on the page (a big photo isn't put in whole)
_RTL = ("R", "AL")


# ----------------------------------------------------------------------------- where things are on a page

class Geo:
    """A page's box and turn: from points on the page as shown (top left, y down) to the PDF's own
    points (bottom left, y up, before the page's turn), and back."""

    def __init__(self, rotation: int, left: float, bottom: float, right: float, top: float):
        self.rotation = rotation % 360
        self.x0, self.y0 = left, bottom
        self.w, self.h = right - left, top - bottom
        self.width, self.height = (self.w, self.h) if self.rotation in (0, 180) else (self.h, self.w)

    def ctm(self) -> tuple:
        """Shown page (y up, as in the new layer's own PDF) -> the page's points."""
        x0, y0, w, h = self.x0, self.y0, self.w, self.h
        return {0: (1, 0, 0, 1, x0, y0), 90: (0, 1, -1, 0, x0 + w, y0), 180: (-1, 0, 0, -1, x0 + w, y0 + h),
                270: (0, -1, 1, 0, x0, y0 + h)}.get(self.rotation, (1, 0, 0, 1, x0, y0))

    def to_user(self, x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.ctm()
        ox, oy = x, self.height - y
        return a * ox + c * oy + e, b * ox + d * oy + f

    def to_display(self, ux: float, uy: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.ctm()
        X, Y = ux - e, uy - f
        return a * X + b * Y, self.height - (c * X + d * Y)

    def rect_to_user(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        pts = [self.to_user(px, py) for px, py in ((x, y), (x + w, y + h))]
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))

    def rect_to_display(self, l: float, b: float, r: float, t: float) -> list[float]:
        pts = [self.to_display(px, py) for px, py in ((l, b), (r, t))]
        x0, x1 = sorted(p[0] for p in pts)
        y0, y1 = sorted(p[1] for p in pts)
        return [x0, y0, x1 - x0, y1 - y0]


def page_geo(page) -> Geo:
    """(a pypdfium2 page)"""
    l, b, r, t = page.get_cropbox()
    return Geo(page.get_rotation(), l, b, r, t)


def pypdf_geo(page) -> Geo:
    box = page.cropbox
    return Geo(page.rotation, float(box.left), float(box.bottom), float(box.right), float(box.top))


# ----------------------------------------------------------------------------- reading a page

def _addr(handle) -> int | None:
    return ctypes.cast(handle, ctypes.c_void_p).value if handle else None


def _bounds(raw) -> tuple[float, float, float, float]:
    import pypdfium2.raw as c
    l, b, r, t = (ctypes.c_float() for _ in range(4))
    c.FPDFPageObj_GetBounds(raw, l, b, r, t)
    return l.value, b.value, r.value, t.value


def _font_name(font) -> tuple[str, int, int, int]:
    """(family, PDF flags, weight, italic angle) of a text's font."""
    import pypdfium2.raw as c
    family = ""
    for get in (c.FPDFFont_GetFamilyName, c.FPDFFont_GetBaseFontName):
        try:
            n = get(font, None, 0)
            if n:
                buf = ctypes.create_string_buffer(n)
                get(font, buf, n)
                family = buf.value.decode("utf-8", "replace")
        except Exception:
            family = ""
        if family:
            break
    angle = ctypes.c_int(0)
    try:
        c.FPDFFont_GetItalicAngle(font, angle)
    except Exception:
        pass
    return family, c.FPDFFont_GetFlags(font), c.FPDFFont_GetWeight(font), angle.value


def clean_family(name: str) -> tuple[str, bool, bool]:
    """"ABCDEF+TimesNewRomanPS-BoldItalicMT" -> ("Times New Roman", bold, italic)."""
    import re
    name = name.split("+", 1)[-1]
    style = ""
    for sep in ("-", ","):
        if sep in name:
            name, style = name.split(sep, 1)
            break
    bold = "bold" in style.lower() or "black" in style.lower() or "heavy" in style.lower()
    italic = "italic" in style.lower() or "oblique" in style.lower()
    for tail in ("PSMT", "MT", "PS"):
        if name.endswith(tail) and len(name) > len(tail) + 2:
            name = name[:-len(tail)]
    if " " not in name:
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return name.strip(), bold, italic


def _strong(text: str) -> str:
    """"R", "L" or "" (no letters that set a direction)."""
    rtl = sum(unicodedata.bidirectional(ch) in _RTL for ch in text)
    ltr = sum(unicodedata.bidirectional(ch) == "L" for ch in text)
    return "" if not rtl and not ltr else "R" if rtl >= ltr else "L"


def line_text(chars: list[tuple[str, float, float]], size: float, spans: list | None = None) -> tuple[str, bool]:
    """The words of one line in reading order, from its letters as the PDF has them ((letter, left,
    right) in the PDF's order). Words are found by the gaps between letters, and put in order by where
    they are (many PDFs keep Arabic in the order it's shown): right to left on an Arabic line, with
    English in it kept in its own order. A word kept backwards is turned around.
    spans: a list to fill with where each word is, [start, end, left, right] (its letters in the text,
    and its place across the line), for finding words on the page exactly."""
    order = {id(t): n for n, t in enumerate(chars)}
    letters = sorted((t for t in chars if not t[0].isspace()), key=lambda t: (t[1] + t[2]) / 2)
    words, cur, right = [], [], None
    for t in letters:
        if cur and t[1] - right > 0.15 * size:
            words.append(cur)
            cur = []
        cur.append(t)
        right = t[2] if right is None or not cur[:-1] else max(right, t[2])
    if cur:
        words.append(cur)
    if not words:
        return "", False

    def text_of(w):
        text = "".join(ch for ch, _, _ in w)
        if _strong(text) != "R":
            return text  # (left to right: in order of where the letters are)
        w = sorted(w, key=lambda t: order[id(t)])  # (Arabic: the PDF's own order inside a word…)
        text = "".join(unicodedata.normalize("NFKC", ch) if "\ufb50" <= ch <= "\ufeff" else ch for ch, _, _ in w)
        if len(w) > 1 and w[0][1] < w[-1][1] - 0.5:
            text = text[::-1]  # (…unless it's kept as shown, left to right)
        return text
    made = [(text_of(w), (w[0][1] + w[-1][2]) / 2, min(t[1] for t in w), max(t[2] for t in w)) for w in words]
    rtl = _strong("".join(t[0] for t in made)) == "R"
    made.sort(key=lambda t: -t[1] if rtl else t[1])
    # words written the other way (English in Arabic, Arabic in English) keep their own order
    other = "L" if rtl else "R"
    out, i = [], 0
    while i < len(made):
        j = i
        while j < len(made) and _strong(made[j][0]) == other:
            j += 1
        if j - i > 1:
            out += list(reversed(made[i:j]))
            i = j
        else:
            out.append(made[i])
            i += 1
    if spans is not None:
        at = 0
        for t in out:
            spans.append([at, at + len(t[0]), round(t[2], 2), round(t[3], 2)])
            at += len(t[0]) + 1
    return arabic_letters(" ".join(t[0] for t in out)), rtl


# Letters that look the same at the start or middle of a word: some fonts (Arial on Windows) give the
# Persian one when a PDF is read back, so Arabic words wouldn't be found. Arabic text gets the Arabic one.
_SAME_JOINED = {"\u06cc": "\u064a", "\u06a9": "\u0643"}  # (Persian yeh, keheh → Arabic yeh, kaf)
_PERSIAN = set("\u067e\u0686\u0698\u06af")  # (پ چ ژ گ: Persian text keeps its own letters)


def arabic_letters(text: str) -> str:
    if not any(ch in _SAME_JOINED for ch in text) or _PERSIAN.intersection(text):
        return text
    out = list(text)
    for i, ch in enumerate(out[:-1]):
        if ch in _SAME_JOINED and "\u0620" <= out[i + 1] <= "\u06ff":
            out[i] = _SAME_JOINED[ch]
    return "".join(out)


def _arabic_unicode(data: bytes, typed: str) -> bytes:
    """Qt's PDF says which letters its shapes are (so the words can be found and copied); for shapes two
    letters share it may name the other one (Persian ی for Arabic ي). Names the letter that was typed."""
    import io
    import re
    from pypdf import PdfReader, PdfWriter
    # (Persian letter, Arabic letter, then their start and middle shapes, which look the same)
    same = (("\u06cc", "\u064a", "\ufbfe", "\ufef3", "\ufbff", "\ufef4"),
            ("\u06a9", "\u0643", "\ufb90", "\ufedb", "\ufb91", "\ufedc"))
    hex_swap = {}
    for persian, arabic, *shapes in same:
        pairs = [(persian, arabic)] + list(zip(shapes[::2], shapes[1::2]))
        if arabic in typed and persian not in typed:
            hex_swap.update({f"{ord(a):04X}": f"{ord(b):04X}" for a, b in pairs})
        elif persian in typed and arabic not in typed:
            hex_swap.update({f"{ord(b):04X}": f"{ord(a):04X}" for a, b in pairs})
    if not hex_swap:
        return data

    def fix_dest(m):
        h = m.group(1).upper()
        return "<" + "".join(hex_swap.get(h[k:k + 4], h[k:k + 4]) for k in range(0, len(h), 4)) + ">"

    def fix(cmap: str) -> str:
        lines, section = [], None
        for line in cmap.splitlines():
            if line.endswith("beginbfrange") or line.endswith("beginbfchar"):
                section = line.split()[-1]
            elif line in ("endbfrange", "endbfchar"):
                section = None
            elif section:
                skip = 2 if section == "beginbfrange" else 1  # (the shapes' numbers come first)
                n = [0]

                def one(m):
                    n[0] += 1  # noqa: B023
                    return m.group(0) if n[0] <= skip else fix_dest(m)  # noqa: B023
                if not (section == "beginbfrange" and "[" not in line and len(line.split()) == 3 and
                        line.split()[0] != line.split()[1]):  # (a run of letters counted up: left alone)
                    line = re.sub(r"<([0-9A-Fa-f]+)>", one, line)
            lines.append(line)
        return "\n".join(lines) + "\n"

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)
    changed = False
    for page in writer.pages:
        fonts = page.get("/Resources", {}).get("/Font", {})
        for ref in fonts.values():
            tu = ref.get_object().get("/ToUnicode")
            if tu is None:
                continue
            tu = tu.get_object()
            old = tu.get_data().decode("latin-1")
            new = fix(old)
            if new != old:
                tu.set_data(new.encode("latin-1"))
                changed = True
    if not changed:
        return data
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ----------------------------------------------------------------------------- every piece, groups too

OBJECT_LIMIT = 20000    # a page's pieces looked at, at most (groups inside groups included)
SHAPE_LIMIT = 1500      # drawn shapes offered on a page, at most (the biggest)
_HIDDEN_TEXT = (3, 7)   # text that isn't drawn (a scan's words, made searchable), or only clips
_DRAWN = None           # PDFium's kinds of pieces (filled in when PDFium is first needed)


def oid(v):
    """A piece's name as the editor keeps it: its place on the page (a number), "3/1" for the second piece of
    the group that is the page's fourth, or "a2" for the page's third annotation."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    v = str(v)
    return int(v) if v.isdigit() else v


def _mx(raw) -> tuple:
    import pypdfium2.raw as c
    m = c.FS_MATRIX()
    if not c.FPDFPageObj_GetMatrix(raw, m):
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    return (m.a, m.b, m.c, m.d, m.e, m.f)


def _mul(m1, m2) -> tuple:
    """m1, then m2."""
    a, b, c, d, e, f = m1
    A, B, C, D, E, F = m2
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _apply(m, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


_ID = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _through(m, box) -> tuple[float, float, float, float]:
    if m == _ID:
        return box
    l, b, r, t = box
    pts = [_apply(m, x, y) for x, y in ((l, b), (r, b), (l, t), (r, t))]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def objects(page_raw, limit: int = OBJECT_LIMIT) -> list[tuple]:
    """Every piece on a page, the ones inside groups (form XObjects) too, in the order they're drawn:
    (name (see oid), PDFium's piece, its kind, how its group's points go to the page's)."""
    import pypdfium2.raw as c
    out: list[tuple] = []

    def walk(count, get, prefix, m, depth):
        for j in range(count):
            if len(out) >= limit:
                return
            o = get(j)
            if not o:
                continue
            name = j if prefix is None else f"{prefix}/{j}"
            kind = c.FPDFPageObj_GetType(o)
            out.append((name, o, kind, m))
            if kind == c.FPDF_PAGEOBJ_FORM and depth < 6:
                walk(c.FPDFFormObj_CountObjects(o), lambda n, o=o: c.FPDFFormObj_GetObject(o, n), str(name),
                     _mul(_mx(o), m), depth + 1)
    walk(c.FPDFPage_CountObjects(page_raw), lambda n: c.FPDFPage_GetObject(page_raw, n), None, _ID, 0)
    return out


def find_object(page_raw, name):
    """(PDFium's piece, the group it's in or None, how that group's points go to the page's) for a name
    (see oid), or (None, None, None)."""
    import pypdfium2.raw as c
    name = oid(name)
    if isinstance(name, int):
        if 0 <= name < c.FPDFPage_CountObjects(page_raw):
            return c.FPDFPage_GetObject(page_raw, name), None, _ID
        return None, None, None
    if not isinstance(name, str) or not name[:1].isdigit():
        return None, None, None
    try:
        steps = [int(s) for s in name.split("/")]
    except ValueError:
        return None, None, None
    if not 0 <= steps[0] < c.FPDFPage_CountObjects(page_raw):
        return None, None, None
    o, parent, m = c.FPDFPage_GetObject(page_raw, steps[0]), None, _ID
    for s in steps[1:]:
        if not o or c.FPDFPageObj_GetType(o) != c.FPDF_PAGEOBJ_FORM or not 0 <= s < c.FPDFFormObj_CountObjects(o):
            return None, None, None
        m = _mul(_mx(o), m)
        parent, o = o, c.FPDFFormObj_GetObject(o, s)
    return (o, parent, m) if o else (None, None, None)


def _rgba(get, raw) -> tuple[int, int, int, int] | None:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not get(raw, r, g, b, a):
        return None
    return r.value, g.value, b.value, a.value


def _hex(rgba) -> str:
    return f"#{rgba[0]:02x}{rgba[1]:02x}{rgba[2]:02x}"


# ----------------------------------------------------------------------------- what can be changed on a page

# a letter PDFium couldn't name (the font doesn't say which letter its shapes are): kept, so the line is still
# there to change, and its words are read from the page's picture instead
UNKNOWN = "�"


def _letter(u: int) -> str:
    if u == 0 or u == 0xFFFD or 0xE000 <= u <= 0xF8FF or (u < 32 and u not in (9, 10, 13)) or 0xFFF0 <= u <= 0xFFFF:
        return UNKNOWN
    try:
        return chr(u)
    except (ValueError, OverflowError):
        return UNKNOWN


def unknown_share(text: str) -> float:
    letters = [ch for ch in text if not ch.isspace()]
    return sum(ch == UNKNOWN for ch in letters) / len(letters) if letters else 0.0


def read_layout(doc, index: int) -> dict:
    """What can be changed on a page: its lines of text (in groups too, turned ones too), pictures, drawn
    shapes and annotations, with where they are, and every letter's box (to highlight words). Call with
    PDFIUM held."""
    import pypdfium2.raw as c
    page = doc[index]
    try:
        geo = page_geo(page)
        area = max(1.0, geo.width * geo.height)
        top: dict[int, object] = {}
        texts: dict = {}
        images, shapes = [], []
        for name, o, kind, m in objects(page.raw):
            top[_addr(o)] = name
            if kind == c.FPDF_PAGEOBJ_IMAGE:
                rect = geo.rect_to_display(*_through(m, _bounds(o)))
                if rect[2] > 2 and rect[3] > 2:
                    images.append({"obj": name, "rect": [round(v, 2) for v in rect]})
            elif kind == c.FPDF_PAGEOBJ_TEXT:
                texts[name] = {"raw": o, "m": m}
            elif kind in (c.FPDF_PAGEOBJ_PATH, c.FPDF_PAGEOBJ_SHADING):
                d = _shape(c, o, kind, m, geo)
                if d is not None:
                    d["obj"] = name
                    shapes.append(d)
        if len(shapes) > SHAPE_LIMIT:
            keep = {id(d) for d in sorted(shapes, key=lambda d: -d["rect"][2] * d["rect"][3])[:SHAPE_LIMIT]}
            shapes = [d for d in shapes if id(d) in keep]
        for d in shapes:
            d["share"] = round(d["rect"][2] * d["rect"][3] / area, 4)
        tp = page.get_textpage()
        try:
            n = c.FPDFText_CountChars(tp.raw)
            chars = []      # (letter, made up by PDFium, piece, box in the PDF's points)
            box = c.FS_RECTF()
            for i in range(n):
                ch = _letter(c.FPDFText_GetUnicode(tp.raw, i))
                gen = c.FPDFText_IsGenerated(tp.raw, i) == 1
                obj = None if gen else top.get(_addr(c.FPDFText_GetTextObject(tp.raw, i)))
                if c.FPDFText_GetLooseCharBox(tp.raw, i, box):
                    rect = (box.left, box.bottom, box.right, box.top)
                else:
                    rect = None
                chars.append((ch, gen, obj, rect))
        finally:
            tp.close()
        lines = _lines(c, geo, chars, texts)
        paras = paragraphs(lines)
        marks = []
        for ch, gen, _obj, rect in chars:
            if rect is None or gen or ch in "\r\n":
                continue
            x, y, w, h = geo.rect_to_display(*rect)
            if w > 0 and h > 0:
                marks.append((round(x, 2), round(y, 2), round(w, 2), round(h, 2)))
        return {"size": (geo.width, geo.height), "lines": lines, "paras": paras, "images": images,
                "shapes": shapes, "annots": _annots(c, doc, page, geo), "chars": marks}
    finally:
        page.close()


def _shape(c, o, kind, m, geo) -> dict | None:
    """A drawn shape (or a colour gradient) that can be moved, deleted or given another colour."""
    rect = geo.rect_to_display(*_through(m, _bounds(o)))
    if rect[2] < 0.3 and rect[3] < 0.3 or rect[2] * rect[3] > 4 * geo.width * geo.height:
        return None
    d = {"obj": None, "rect": [round(v, 2) for v in rect], "kind": "gradient"}
    if kind == c.FPDF_PAGEOBJ_PATH:
        mode, stroke = ctypes.c_int(0), ctypes.c_int(0)
        if not c.FPDFPath_GetDrawMode(o, mode, stroke):
            return None
        if not mode.value and not stroke.value:
            return None  # (a path that only clips, drawn with nothing)
        d["kind"] = "path"
        fill = _rgba(c.FPDFPageObj_GetFillColor, o) if mode.value else None
        line = _rgba(c.FPDFPageObj_GetStrokeColor, o) if stroke.value else None
        if fill is not None:
            d["fill"] = _hex(fill)
        if line is not None:
            d["color"] = _hex(line)
            width = ctypes.c_float(0)
            if c.FPDFPageObj_GetStrokeWidth(o, width):
                d["width"] = round(width.value, 2)
    return d


_ANNOT_KINDS = {1: "note", 2: "link", 3: "freetext", 4: "line", 5: "square", 6: "circle", 7: "polygon",
                8: "polyline", 9: "highlight", 10: "underline", 11: "squiggly", 12: "strike", 13: "stamp",
                14: "caret", 15: "ink", 17: "attachment", 18: "sound", 28: "redact"}


def _annot_text(c, annot, key: str) -> str:
    try:
        n = c.FPDFAnnot_GetStringValue(annot, key.encode(), None, 0)
        if n <= 2:
            return ""
        buf = ctypes.create_string_buffer(n)
        c.FPDFAnnot_GetStringValue(annot, key.encode(), ctypes.cast(buf, ctypes.POINTER(c.FPDF_WCHAR)), n)
        return buf.raw[:n - 2].decode("utf-16-le", "replace")
    except Exception:
        return ""


def _annots(c, doc, page, geo) -> list[dict]:
    """The page's annotations (notes, highlights, drawings, stamps, links… not form boxes): where they are,
    and what they say or point to."""
    out = []
    for n in range(c.FPDFPage_GetAnnotCount(page.raw)):
        annot = c.FPDFPage_GetAnnot(page.raw, n)
        if not annot:
            continue
        try:
            sub = c.FPDFAnnot_GetSubtype(annot)
            kind = _ANNOT_KINDS.get(sub)
            if kind is None or c.FPDFAnnot_GetFlags(annot) & 2:  # (form boxes, pop-ups, hidden ones…)
                continue
            r = c.FS_RECTF()
            if not c.FPDFAnnot_GetRect(annot, r):
                continue
            rect = geo.rect_to_display(min(r.left, r.right), min(r.bottom, r.top), max(r.left, r.right),
                                       max(r.bottom, r.top))
            if rect[2] < 1 or rect[3] < 1:
                continue
            d = {"obj": f"a{n}", "kind": kind, "rect": [round(v, 2) for v in rect],
                 "text": _annot_text(c, annot, "Contents")}
            if kind == "link":
                link = c.FPDFAnnot_GetLink(annot)
                action = c.FPDFLink_GetAction(link) if link else None
                if action and c.FPDFAction_GetType(action) == c.PDFACTION_URI:
                    size = c.FPDFAction_GetURIPath(doc.raw, action, None, 0)
                    if size > 1:
                        buf = ctypes.create_string_buffer(size)
                        c.FPDFAction_GetURIPath(doc.raw, action, buf, size)
                        d["url"] = buf.value.decode("latin-1", "replace")
                dest = c.FPDFLink_GetDest(doc.raw, link) if link else None
                if not dest and action and c.FPDFAction_GetType(action) == c.PDFACTION_GOTO:
                    dest = c.FPDFAction_GetDest(doc.raw, action)
                if dest:
                    d["target"] = c.FPDFDest_GetDestPageIndex(doc.raw, dest)
            out.append(d)
        except Exception:
            log.debug("an annotation couldn't be read", exc_info=True)
        finally:
            c.FPDFPage_CloseAnnot(annot)
    return out


def _lines(c, geo: Geo, chars: list, texts: dict) -> list[dict]:
    """Letters -> lines that can be changed: letters next to each other on a line, from the page's text (in
    groups too). Lines that run across the page left to right are put together from the letters as shown;
    a turned line (up, down, slanted) is one piece of text or a few in a row."""
    info: dict = {}

    def obj_info(k) -> dict:
        if k in info:
            return info[k]
        raw, parent = texts[k]["raw"], texts[k]["m"]
        m = _mul(_mx(raw), parent)
        size = ctypes.c_float(0)
        c.FPDFTextObj_GetFontSize(raw, size)
        scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1
        # the way the line runs, as shown
        p0, p1 = geo.to_display(m[4], m[5]), geo.to_display(m[4] + m[0], m[5] + m[1])
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        straight = dx > 0 and abs(dy) < 0.02 * dx
        family, flags, weight, angle = _font_name(c.FPDFTextObj_GetFont(raw))
        name, bold, italic = clean_family(family)
        rgba = _rgba(c.FPDFPageObj_GetFillColor, raw) or (0, 0, 0, 255)
        try:
            hidden = c.FPDFTextObj_GetTextRenderMode(raw) in _HIDDEN_TEXT
        except Exception:
            hidden = False
        info[k] = {"size": size.value * scale, "straight": straight, "font": name,
                   "serif": bool(flags & 2), "fixed": bool(flags & 1),
                   "bold": bold or weight >= 600 or bool(flags & (1 << 18)), "italic": italic or angle != 0,
                   "color": _hex(rgba), "bounds": _through(parent, _bounds(raw)),
                   "base": p0[1], "origin": p0, "turn": math.degrees(math.atan2(dy, dx)) if dx or dy else 0.0,
                   "hidden": hidden}
        return info[k]

    # letters into runs: the letters of one line (as PDFium reads the page), split where there's a big
    # gap (a table's columns) or the size changes
    runs: list[list] = []
    line: list = []
    turned: dict = {}  # (piece: its letters, for the turned lines)

    def end_line():
        line.sort(key=lambda t: t[2][0])
        cur, right, size = [], None, 0.0
        for t in line:
            o = info[t[1]]
            if cur and (t[2][0] - right > 1.6 * max(size, o["size"]) or
                        abs(o["size"] - size) > 0.35 * max(size, o["size"])):
                runs.append(cur)
                cur = []
            if not cur:
                right, size = t[2][2], o["size"]
            cur.append(t)
            right = max(right, t[2][2])
        if cur:
            runs.append(cur)
        line.clear()
    for n, (ch, gen, obj, rect) in enumerate(chars):
        if ch in "\r\n":
            end_line()
            continue
        if gen or obj is None or obj not in texts or rect is None or ch.isspace():
            continue
        o = obj_info(obj)
        if o["size"] <= 0.5:
            continue
        x, y, w, h = geo.rect_to_display(*rect)
        if o["straight"]:
            line.append((ch, obj, (x, y, x + w, y + h), n))
        else:
            turned.setdefault(obj, []).append((ch, (x, y, x + w, y + h), n))
    end_line()

    # one PDF text piece belongs to one line only (it's taken out whole)
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner: dict = {}
    for i, run in enumerate(runs):
        for _, obj, _, _ in run:
            if obj in owner:
                parent[find(i)] = find(owner[obj])
            else:
                owner[obj] = i
    groups: dict[int, list] = {}
    for i, _run in enumerate(runs):
        groups.setdefault(find(i), []).append(i)
    lines = []
    for members in groups.values():
        letters = sorted((x for i in members for x in runs[i]), key=lambda t: t[3])
        objs = sorted({obj for _, obj, _, _ in letters}, key=str)
        if not objs:
            continue
        shown = [(ch, box[0], box[2]) for ch, _, box, _ in letters]
        main = max(objs, key=lambda k: sum(1 for t in letters if t[1] == k))
        spans: list = []
        text, rtl = line_text(shown, info[main]["size"], spans)
        if not text.strip():
            continue
        l = min(info[k]["bounds"][0] for k in objs)
        b = min(info[k]["bounds"][1] for k in objs)
        r = max(info[k]["bounds"][2] for k in objs)
        t = max(info[k]["bounds"][3] for k in objs)
        line = _line_of(info, main, objs, text, rtl, geo.rect_to_display(l, b, r, t))
        line["words"] = spans
        # Arabic and English on one line are often in two fonts: the font of each
        for script, kinds in (("ar", _RTL), ("en", ("L",))):
            used = [obj for ch, obj, _, _ in letters if unicodedata.bidirectional(ch) in kinds]
            if used:
                line["font_" + script] = info[max(set(used), key=used.count)]["font"]
        lines.append(line)
    lines += _turned_lines(info, turned, geo)
    lines.sort(key=lambda d: (round(d["rect"][1]), d["rect"][0]))
    return lines


# ----------------------------------------------------------------------------- words read from the page's picture

READ_DPI = 300          # a page is read at this sharpness (Windows reads print-size text best around here)


def needs_reading(layout: dict) -> bool:
    """A page whose words can only be read from its picture: lines in a font that doesn't say which letters it
    has, or a scan (a big picture and no text of its own)."""
    lines = layout.get("lines") or []
    if any(d.get("unknown") for d in lines):
        return True
    if lines:
        return False
    w, h = layout.get("size") or (1, 1)
    area = max(1.0, w * h)
    return any(d["rect"][2] * d["rect"][3] >= 0.4 * area for d in layout.get("images") or ())


def page_picture(doc, index: int):
    """(the page drawn for reading, as a Pillow picture; points to its pixels). Call with PDFIUM held."""
    page = doc[index]
    try:
        w, h = page.get_size()
        scale = min(READ_DPI / 72, 5000 / max(1.0, w, h))
        bitmap = page.render(scale=scale, may_draw_forms=False)
        try:
            return bitmap.to_pil().convert("RGB"), scale
        finally:
            bitmap.close()
    finally:
        page.close()


def ocr_lines(lines, scale: float) -> list[dict]:
    """Windows' lines of words (extras.read_lines) -> {"text", "rtl", "rect"} in page points."""
    out = []
    for line in lines:
        words = [w for w in line.words if w.text.strip()]
        if not words or abs(getattr(line, "angle", 0.0) or 0.0) > 3:
            continue
        x0 = min(w.x for w in words) / scale
        y0 = min(w.y for w in words) / scale
        x1 = max(w.x + w.w for w in words) / scale
        y1 = max(w.y + w.h for w in words) / scale
        out.append({"text": arabic_letters(line.text), "rtl": bool(line.rtl),
                    "rect": [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)]})
    return out


def _share_over(a, b) -> float:
    """How much of box a (x, y, w, h) is over box b."""
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h) / max(0.01, a[2] * a[3])


def with_read_words(layout: dict, read: list[dict]) -> list[dict]:
    """The words read from the page's picture, put where they belong: a line in a font that doesn't say which
    letters it has gets them ("read"), and the rest of the words (a scan's) become lines of their own that can
    be changed (they paint over the old words, see patch). Gives those new lines."""
    lines = layout.get("lines") or []

    def place(d):  # (a line whose letters have no shapes PDFium can measure: as tall as its text)
        x, y, w, h = d["rect"]
        size = float(d.get("size") or 0)
        if h < 0.5 * size:
            return [x, (d.get("base") or y + h) - 0.85 * size, max(w, 1.0), size]
        return d["rect"]
    for d in lines:
        if d.get("unknown"):
            got = [r for r in read if _share_over(r["rect"], place(d)) > 0.5 or _share_over(place(d), r["rect"]) > 0.5]
            if got:
                d["read"] = " ".join(r["text"] for r in sorted(got, key=lambda r: (-r["rect"][0] if r["rtl"] else
                                                                                   r["rect"][0])))
    made = []
    for r in read:
        if any(_share_over(r["rect"], place(d)) > 0.3 for d in lines):
            continue  # (the PDF has its own text there)
        x, y, w, h = r["rect"]
        size = round(max(4.0, h * 0.82), 1)
        made.append({"objs": [f"s{len(made)}"], "rect": [x, y, w, h], "text": r["text"], "rtl": r["rtl"],
                     "size": size, "font": "Arial", "serif": False, "fixed": False, "bold": False, "italic": False,
                     "color": "#000000", "base": round(y + h * 0.8, 2), "scan": True, "words": []})
    return made


def _line_of(info: dict, main, objs: list, text: str, rtl: bool, rect) -> dict:
    o = info[main]
    font = o["font"] or ("Courier New" if o["fixed"] else "Times New Roman" if o["serif"] else "Arial")
    line = {"objs": objs, "rect": [round(v, 2) for v in rect], "text": text, "rtl": rtl,
            "size": round(o["size"], 2), "font": font, "serif": o["serif"], "fixed": o["fixed"], "bold": o["bold"],
            "italic": o["italic"], "color": o["color"], "base": round(o["base"], 2)}
    if all(info[k]["hidden"] for k in objs):
        line["hidden"] = True  # (words over a scan, made searchable: what shows is the scan's picture)
    if unknown_share(text) > 0.3:
        line["unknown"] = True  # (a font that doesn't say which letters it has: the words are read from the page)
    return line


def _turned_lines(info: dict, turned: dict, geo: Geo) -> list[dict]:
    """Lines that don't run left to right across the page: each piece of text on its own, with the pieces
    right after it on the same line (the same turn and size) put with it."""
    out: list[dict] = []
    order = sorted(turned, key=lambda k: turned[k][0][2])
    cur: list = []

    def flush():
        if not cur:
            return
        first = info[cur[0]]
        a = math.radians(first["turn"])
        ux, uy = math.cos(a), math.sin(a)
        ox, oy = first["origin"]
        shown, starts = [], []
        for k in cur:
            sx, sy = info[k]["origin"]
            starts.append(((sx - ox) * ux + (sy - oy) * uy, k))
            for ch, (x0, y0, x1, y1), _n in turned[k]:
                along = [(px - ox) * ux + (py - oy) * uy for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
                shown.append((ch, min(along), max(along)))
        text, rtl = line_text(shown, first["size"])
        if text.strip():
            start, k0 = min(starts, key=lambda t: t[0])
            l = min(info[k]["bounds"][0] for k in cur)
            b = min(info[k]["bounds"][1] for k in cur)
            r = max(info[k]["bounds"][2] for k in cur)
            t = max(info[k]["bounds"][3] for k in cur)
            line = _line_of(info, k0, sorted(cur, key=str), text, rtl, geo.rect_to_display(l, b, r, t))
            sx, sy = info[k0]["origin"]
            line.update(turn=round(first["turn"], 2), origin=[round(sx, 2), round(sy, 2)], words=[])
            line.pop("base", None)
            out.append(line)
        cur.clear()
    for k in order:
        if cur:
            last = info[cur[-1]]
            o = info[k]
            a = math.radians(last["turn"])
            ux, uy = math.cos(a), math.sin(a)
            dx, dy = o["origin"][0] - last["origin"][0], o["origin"][1] - last["origin"][1]
            across = abs(-dx * uy + dy * ux)
            along = dx * ux + dy * uy
            if not (abs(o["turn"] - last["turn"]) < 1 and abs(o["size"] - last["size"]) <= 0.1 * last["size"]
                    and across < 0.3 * last["size"] and -0.5 * last["size"] < along < 60 * last["size"]):
                flush()
        cur.append(k)
    flush()
    return out


_BULLET = re.compile(r"^\s*([\u2022\u25cf\u25aa\u25e6\u2013\u2014*\-]|\(?([0-9\u0660-\u0669]{1,3}|[a-zA-Z])[.)])\s")


def _same_look(a: dict, b: dict) -> bool:
    return (a["font"] == b["font"] and a["bold"] == b["bold"] and a["italic"] == b["italic"] and
            a["color"] == b["color"] and a["rtl"] == b["rtl"] and
            abs(a["size"] - b["size"]) <= 0.08 * max(a["size"], b["size"]))


def _first_word_width(line: dict) -> float:
    words = line.get("words") or []
    if not words:
        return line["rect"][2]
    return words[0][3] - words[0][2]


def paragraphs(lines: list[dict]) -> list[dict]:
    """Lines that are one paragraph (wrapped text: the same look, evenly spaced, lined up, and each line
    full enough that the next line's first word wouldn't have fitted on it) put together, so a paragraph
    can be changed as a whole and its words flow from line to line again. Gives
    {"lines": [the lines' places], "rect", "align", "spacing"} (spacing: from one line to the next, in
    text sizes); lines alone aren't in it. Sets each line's "para" to its paragraph's place.
    lines: as read_layout gives them (top to bottom)."""
    groups: list[list[int]] = []
    near: list[int] = []  # (the groups whose last line is just above: the only ones a line can join)
    for i, line in enumerate(lines):
        if "base" not in line:
            continue  # (a turned line is a line of its own)
        size = line["size"]
        x, _y, w, _h = line["rect"]
        near = [n for n in near if lines[groups[n][-1]]["base"] > line["base"] - 2.5 * size]
        joined = None
        for n in near:
            group = groups[n]
            last = lines[group[-1]]
            if not _same_look(last, line) or _BULLET.match(line["text"]):
                continue
            pitch = line["base"] - last["base"]
            if not 0.95 * size <= pitch <= 1.85 * size:
                continue
            if len(group) > 1:
                was = (last["base"] - lines[group[0]]["base"]) / (len(group) - 1)
                if abs(pitch - was) > 0.12 * was:
                    continue
            lx, _, lw, _ = last["rect"]
            if min(lx + lw, x + w) - max(lx, x) < 0.5 * min(lw, w):
                continue
            if len(last.get("words") or ()) < 4 and lw < 20 * size:
                continue  # (a short line like "Name: Ahmad" over "Phone: 555": a form's lines, not wrapped text)
            members = [lines[k]["rect"] for k in group] + [line["rect"]]
            left = min(r[0] for r in members)
            right = max(r[0] + r[2] for r in members)
            tol = 0.75 * size
            lefts = all(abs(r[0] - left) <= tol for r in members)
            rights = all(abs(r[0] + r[2] - right) <= tol for r in members)
            centers = all(abs(r[0] + r[2] / 2 - (left + right) / 2) <= tol for r in members)
            if not (lefts or rights or centers):
                continue
            # the line before was full: the next line's first word wouldn't have fitted at its end
            if lefts and not line["rtl"]:
                room = right - (lx + lw)
            elif rights:
                room = lx - left
            else:
                room = (right - left) - lw
            if room > _first_word_width(line) + 0.6 * size:
                continue
            joined = n
            break
        if joined is None:
            groups.append([i])
            near.append(len(groups) - 1)
        else:
            groups[joined].append(i)
    paras: list[dict] = []
    for g in groups:
        if len(g) < 2:
            continue
        rects = [lines[k]["rect"] for k in g]
        size = lines[g[0]]["size"]
        left = min(r[0] for r in rects)
        top = min(r[1] for r in rects)
        right = max(r[0] + r[2] for r in rects)
        bottom = max(r[1] + r[3] for r in rects)
        tol = 0.75 * size
        lefts = all(abs(r[0] - left) <= tol for r in rects)
        rights = all(abs(r[0] + r[2] - right) <= tol for r in rects)
        body = rects[:-1]
        if len(body) >= 2 and all(abs(r[0] + r[2] - right) <= 0.3 * size and abs(r[0] - left) <= 0.3 * size
                                  for r in body):
            align = "justify"
        elif lines[g[0]]["rtl"]:
            align = "right" if rights else "center" if not lefts else "left"
        else:
            align = "left" if lefts else "right" if rights else "center"
        pitch = (lines[g[-1]]["base"] - lines[g[0]]["base"]) / (len(g) - 1)
        for k in g:
            lines[k]["para"] = len(paras)
        paras.append({"lines": list(g), "rect": [round(left, 2), round(top, 2), round(right - left, 2),
                                                 round(bottom - top, 2)],
                      "align": align, "spacing": round(pitch / size, 3)})
    return paras


def save_picture(src: Path, password: str, index: int, obj: int, out: Path):
    """A copy of one of a page's pictures (the page's piece obj), as a PNG file."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    with PDFIUM:
        doc = pdfium.PdfDocument(str(src), password=password or None)
        try:
            page = doc[index]
            try:
                raw = c.FPDFPage_GetObject(page.raw, obj)
                if not raw or c.FPDFPageObj_GetType(raw) != c.FPDF_PAGEOBJ_IMAGE:
                    raise RuntimeError("not a picture")
                img = pdfium.PdfImage(raw, page=page, pdf=doc)
                try:
                    bitmap = img.get_bitmap(render=False)
                except Exception:
                    bitmap = img.get_bitmap(render=True)
                pil = bitmap.to_pil()
            finally:
                page.close()
        finally:
            doc.close()
    if pil.mode not in ("RGB", "RGBA", "L", "LA"):
        pil = pil.convert("RGBA" if "A" in pil.mode else "RGB")
    tmp = out.with_name(out.name + ".part")
    pil.save(tmp, "PNG")
    tmp.replace(out)


def _color(text: str) -> tuple[int, int, int, int] | None:
    text = str(text or "").lstrip("#")
    if len(text) != 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), 255
    except ValueError:
        return None


def _paint(c, o, look: dict):
    """A drawn shape given the colours picked in the editor (look: color = its line's, fill = inside it)."""
    for key, getter, setter in (("fill", c.FPDFPageObj_GetFillColor, c.FPDFPageObj_SetFillColor),
                                ("color", c.FPDFPageObj_GetStrokeColor, c.FPDFPageObj_SetStrokeColor)):
        rgba = _color(look.get(key, ""))
        if rgba is None:
            continue
        old = _rgba(getter, o)
        setter(o, rgba[0], rgba[1], rgba[2], old[3] if old else 255)


def render(doc, index: int, scale: float, hide: tuple = (), paint: dict | None = None) -> tuple[bytes, int, int, int]:
    """A picture of the page (RGBX), without the pieces that were changed, moved or deleted (hide: their
    names, see oid; annotations too), and with shapes given other colours (paint: {name: look}).
    Call with PDFIUM held. (The page itself isn't changed: a page loaded again is as it was.)"""
    import pypdfium2.raw as c
    page = doc[index]
    taken = []
    flags = []
    try:
        for name, look in (paint or {}).items():
            o, _parent, _m = find_object(page.raw, name)
            if o:
                _paint(c, o, look)
        found = [(name, *find_object(page.raw, name)) for name in hide if not str(name).startswith("a")]
        for _name, o, parent, _m in found:  # (all found first: taking one out changes the places after it)
            if not o:
                continue
            if parent is None:
                ok = c.FPDFPage_RemoveObject(page.raw, o)
            else:
                ok = c.FPDFFormObj_RemoveObject(parent, o)
            if ok:
                taken.append(o)
        for name in hide:
            if str(name).startswith("a") and str(name)[1:].isdigit():
                annot = c.FPDFPage_GetAnnot(page.raw, int(str(name)[1:]))
                if annot:
                    flags.append((annot, c.FPDFAnnot_GetFlags(annot)))
                    c.FPDFAnnot_SetFlags(annot, flags[-1][1] | 2)
        bitmap = page.render(scale=scale, may_draw_forms=False, rev_byteorder=True, prefer_bgrx=True)
        try:
            data = bytes(bitmap.buffer)
            return data, bitmap.width, bitmap.height, bitmap.stride
        finally:
            bitmap.close()
    finally:
        for annot, was in flags:
            c.FPDFAnnot_SetFlags(annot, was)
            c.FPDFPage_CloseAnnot(annot)
        page.close()
        for o in taken:
            c.FPDFPageObj_Destroy(o)


# ----------------------------------------------------------------------------- form boxes

def _full_name(field) -> str:
    names = []
    seen = 0
    while field is not None and seen < 32:
        if "/T" in field:
            names.append(str(field["/T"]))
        field = field.get("/Parent")
        field = field.get_object() if field is not None else None
        seen += 1
    return ".".join(reversed(names))


def _inherited(field, key: str):
    seen = 0
    while field is not None and seen < 32:
        if key in field:
            return field[key]
        field = field.get("/Parent")
        field = field.get_object() if field is not None else None
        seen += 1
    return None


def _on_state(widget) -> str:
    try:
        for name in widget["/AP"]["/N"].keys():
            if name != "/Off":
                return str(name)
    except Exception:
        pass
    return "/Yes"


def read_fields(reader) -> list[dict]:
    """The form's boxes on every page: name, kind ("text", "check", "radio", "choice"), value, place."""
    out = []
    for pi, page in enumerate(reader.pages):
        geo = pypdf_geo(page)
        for ref in page.get("/Annots") or []:
            try:
                w = ref.get_object()
                if w.get("/Subtype") != "/Widget":
                    continue
                kind = _inherited(w, "/FT")
                flags = int(_inherited(w, "/Ff") or 0)
                name = _full_name(w)
                l, b, r, t = (float(v) for v in w["/Rect"])
                rect = geo.rect_to_display(min(l, r), min(b, t), max(l, r), max(b, t))
                value = _inherited(w, "/V")
                field = {"page": pi, "name": name, "rect": [round(v, 2) for v in rect]}
                if flags & 1:
                    field["locked"] = True  # (read only in other PDF apps: it can still be changed here)
                if kind == "/Tx":
                    da = str(_inherited(w, "/DA") or "")
                    size = 0.0
                    bits = da.split()
                    if "Tf" in bits and bits.index("Tf") >= 1:
                        try:
                            size = float(bits[bits.index("Tf") - 1])
                        except ValueError:
                            size = 0.0
                    field.update(kind="text", value=str(value) if value is not None else "",
                                 multiline=bool(flags & (1 << 12)), secret=bool(flags & (1 << 13)), size=size,
                                 limit=int(_inherited(w, "/MaxLen") or 0))
                elif kind == "/Btn":
                    if flags & (1 << 16):  # a push button
                        continue
                    on = _on_state(w)
                    radio = bool(flags & (1 << 15))
                    field.update(kind="radio" if radio else "check", on=on,
                                 value=str(value) if value is not None else "/Off")
                elif kind == "/Ch":
                    opts, exports = [], []
                    for o in _inherited(w, "/Opt") or []:
                        o = o.get_object() if hasattr(o, "get_object") else o
                        pair = isinstance(o, list) and len(o) > 1
                        opts.append(str(o[1] if pair else o))
                        exports.append(str(o[0] if pair else o))
                    if isinstance(value, list):
                        value = value[0] if value else ""
                    value = str(value) if value is not None else ""
                    if value in exports:  # (the form keeps a choice's own value: the words shown are its name)
                        value = opts[exports.index(value)]
                    field.update(kind="choice", options=opts, exports=exports, value=value)
                else:
                    continue
                out.append(field)
            except Exception:
                log.debug("a form box couldn't be read", exc_info=True)
    return out


# ----------------------------------------------------------------------------- writing the new copy

def font_for(d: dict):
    from PySide6.QtGui import QFont
    f = QFont(d.get("font") or "Arial")
    f.setPointSizeF(max(1.0, float(d.get("size", 12))))
    f.setBold(bool(d.get("bold")))
    f.setItalic(bool(d.get("italic")))
    f.setUnderline(bool(d.get("underline")))
    f.setHintingPreference(QFont.PreferNoHinting)
    f.setStyleStrategy(QFont.PreferQuality)
    return f


_LAYOUT_DEVICE = None


def layout_device():
    """Text is laid out as if on a 72 dpi page, on the screen and in the PDF alike: a point is a point,
    so the words wrap and sit exactly where they were typed."""
    global _LAYOUT_DEVICE
    if _LAYOUT_DEVICE is None:
        from PySide6.QtGui import QImage
        img = QImage(4, 4, QImage.Format.Format_ARGB32)
        img.setDotsPerMeterX(round(72 / 0.0254))
        img.setDotsPerMeterY(round(72 / 0.0254))
        _LAYOUT_DEVICE = img
    return _LAYOUT_DEVICE


TEXT_MARGIN = 1.0
ALIGNS: dict = {}  # (filled in when Qt is first needed: see _aligns)


def _aligns():
    from PySide6.QtCore import Qt as _Qt
    if not ALIGNS:
        ALIGNS.update({"left": _Qt.AlignLeft | _Qt.AlignAbsolute, "center": _Qt.AlignHCenter,
                       "right": _Qt.AlignRight | _Qt.AlignAbsolute, "justify": _Qt.AlignJustify})
    return ALIGNS


def text_document(d: dict, doc=None):
    """The QTextDocument a text box is drawn with (the editor's text boxes use the same settings).
    d: font, size, bold, italic, underline, color; width (wraps the words in a box that wide, as a paragraph
    does), align (left, center, right, justify), spacing (from one line to the next, in text sizes)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextOption
    if doc is None:
        doc = QTextDocument()
        doc.setPlainText(d.get("text", ""))
    doc.documentLayout().setPaintDevice(layout_device())
    doc.setUseDesignMetrics(True)
    doc.setDocumentMargin(TEXT_MARGIN)
    doc.setDefaultFont(font_for(d))
    opt = QTextOption(doc.defaultTextOption())
    opt.setWrapMode(QTextOption.NoWrap if not d.get("width") else QTextOption.WordWrap)
    doc.setDefaultTextOption(opt)
    fmt = QTextCharFormat()
    fmt.setFont(font_for(d))
    fmt.setForeground(QColor(d.get("color", "#000000")))
    cur = QTextCursor(doc)
    cur.select(QTextCursor.Document)
    cur.mergeCharFormat(fmt)
    block = QTextBlockFormat()
    block.setAlignment(_aligns().get(d.get("align") or "", Qt.AlignLeading))
    if d.get("spacing"):
        block.setLineHeight(max(0.5, float(d["spacing"])) * max(1.0, float(d.get("size", 12))),
                            QTextBlockFormat.LineHeightTypes.FixedHeight.value)
    else:
        block.setLineHeight(100, QTextBlockFormat.LineHeightTypes.SingleHeight.value)
    cur.mergeBlockFormat(block)
    arabic_font(d, doc)
    # (as wide as its longest line: right-to-left lines then line up on the right)
    doc.setTextWidth(float(d["width"]) if d.get("width") else -1)
    if not d.get("width"):
        doc.setTextWidth(doc.idealWidth())
    return doc


def arabic_font(d: dict, doc):
    """A changed line whose Arabic words were in a font of their own (font_ar): they're kept in it."""
    if not d.get("font_ar"):
        return
    from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
    fmt = QTextCharFormat()
    fmt.setFont(font_for(dict(d, font=d["font_ar"])))
    fmt.setForeground(QColor(d.get("color", "#000000")))
    text = doc.toPlainText()
    cur = QTextCursor(doc)
    i = 0
    while i < len(text):
        if unicodedata.bidirectional(text[i]) not in _RTL:
            i += 1
            continue
        j = i
        while j < len(text) and (unicodedata.bidirectional(text[j]) in _RTL or
                                 unicodedata.bidirectional(text[j]) in ("NSM", "WS", "ON", "AN") and text[j] != "\n"):
            j += 1
        cur.setPosition(i)
        cur.setPosition(j, QTextCursor.KeepAnchor)
        cur.setCharFormat(fmt)
        i = j


def _arrow_head(p, x1, y1, x2, y2, width):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    ang = math.atan2(y2 - y1, x2 - x1)
    size = max(6.0, width * 4)
    pts = [QPointF(x2, y2)]
    for da in (2.6, -2.6):
        pts.append(QPointF(x2 + size * math.cos(ang + da), y2 + size * math.sin(ang + da)))
    p.drawPolygon(QPolygonF(pts))


def draw_mark(p, d: dict):
    """Draws one added thing (a dict from the editor) with a QPainter, in page points."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QImage, QPainterPath, QPen
    kind = d.get("kind")
    color = QColor(d.get("color", "#000000"))
    width = float(d.get("width", 2))
    p.save()
    try:
        if kind == "text" and d.get("patch"):  # (changed words of a scan: the old ones are painted over first)
            patch = list(d["patch"])
            p.fillRect(QRectF(*(float(v) for v in patch[:4])), QColor(patch[4] if len(patch) > 4 else "#ffffff"))
        if d.get("opacity") is not None and kind != "cover":  # (a cover always hides all of what's under it)
            p.setOpacity(min(1.0, max(0.05, float(d["opacity"]))))
        if kind == "text":
            doc = text_document(d)
            p.translate(float(d["x"]), float(d["y"]))
            turn = float(d.get("turn") or 0)
            if turn:  # (a turned line: turned around where its words start)
                px, py = (float(v) for v in (d.get("pivot") or (0, 0)))
                p.translate(px, py)
                p.rotate(turn)
                p.translate(-px, -py)
            doc.drawContents(p)
        elif kind == "image":
            img = QImage(str(d.get("path", "")))
            if img.isNull():
                raise RuntimeError(tr("a picture that was added can't be found any more"))
            rect = QRectF(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]))
            most = max(16, round(max(rect.width(), rect.height()) / 72 * PICTURE_DPI))
            if max(img.width(), img.height()) > most:
                img = img.scaled(most, most, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.setRenderHint(p.RenderHint.SmoothPixmapTransform)
            turn = int(d.get("turn", 0)) % 360
            if turn:
                # (turned: the box is the turned picture's, so the picture itself is drawn on its side in it)
                p.translate(rect.center())
                p.rotate(turn)
                w, h = (rect.height(), rect.width()) if turn in (90, 270) else (rect.width(), rect.height())
                rect = QRectF(-w / 2, -h / 2, w, h)
            p.drawImage(rect, img)
        elif kind == "cover":
            p.fillRect(QRectF(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"])), color)
        elif kind in ("underline", "strike"):
            for x, y, w, h in d.get("rects", []):
                t = max(0.6, h * 0.07)
                ly = y + h * (0.9 if kind == "underline" else 0.55)
                p.fillRect(QRectF(x, ly - t / 2, w, t), color)
        elif kind == "ink":
            pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            p.setRenderHint(p.RenderHint.Antialiasing)
            for stroke in d.get("strokes", []):
                if not stroke:
                    continue
                path = QPainterPath(QPointF(*stroke[0]))
                for pt in stroke[1:]:
                    path.lineTo(QPointF(*pt))
                if len(stroke) == 1:
                    p.drawPoint(QPointF(*stroke[0]))
                else:
                    p.drawPath(path)
        elif kind in ("rect", "ellipse", "line", "arrow"):
            x1, y1, x2, y2 = (float(v) for v in d["points"])
            p.setRenderHint(p.RenderHint.Antialiasing)
            p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(QBrush(QColor(d["fill"])) if d.get("fill") else Qt.NoBrush)
            rect = QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()
            if kind == "rect":
                p.drawRect(rect)
            elif kind == "ellipse":
                p.drawEllipse(rect)
            else:
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                if kind == "arrow":
                    p.setBrush(color)
                    _arrow_head(p, x1, y1, x2, y2, width)
    finally:
        p.restore()


DRAWN = ("text", "image", "cover", "underline", "strike", "ink", "rect", "ellipse", "line", "arrow")


def _layer(pages: dict[int, list[dict]], sizes: dict[int, tuple[float, float]]) -> bytes:
    """The added things of every page as a PDF of their own (one page each, in the order given)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    writer = QPdfWriter(buf)
    writer.setResolution(72)
    writer.setCreator("Rakiza Core")

    def size_page(i):
        w, h = sizes[i]
        # (ExactMatch: a page a little off A4 or Letter isn't given that size instead. Qt still rounds to whole
        # points: save_edits makes up for the difference)
        writer.setPageLayout(QPageLayout(QPageSize(QSizeF(w, h), QPageSize.Point, "", QPageSize.ExactMatch),
                                         QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    order = list(pages)
    size_page(order[0])
    p = QPainter(writer)
    try:
        for n, i in enumerate(order):
            if n:
                size_page(i)
                writer.newPage()
            for d in pages[i]:
                draw_mark(p, d)
    finally:
        p.end()
    buf.close()
    typed = "".join(d.get("text", "") for marks in pages.values() for d in marks if d.get("kind") == "text")
    return _arabic_unicode(bytes(data), typed)


def _highlights(writer, page, geo: Geo, marks: list[dict]):
    """Highlights straight into the page: see-through colour that darkens (multiply), so the words stay
    black and a highlight on a scan looks like a real highlighter."""
    from pypdf import PageObject, Transformation
    from pypdf.generic import ContentStream, DictionaryObject, FloatObject, NameObject
    ops = ["q"]
    for d in marks:
        from PySide6.QtGui import QColor
        col = QColor(d.get("color", "#ffd400"))
        ops.append(f"/CfHi gs {col.redF():.3f} {col.greenF():.3f} {col.blueF():.3f} rg")
        for x, y, w, h in d.get("rects", []):
            ops.append(f"{x:.2f} {geo.height - y - h:.2f} {w:.2f} {h:.2f} re f")
    ops.append("Q")
    over = PageObject.create_blank_page(writer, width=geo.width, height=geo.height)
    gs = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"), NameObject("/BM"): NameObject("/Multiply"),
                           NameObject("/ca"): FloatObject(HIGHLIGHT_BLEND)})
    over[NameObject("/Resources")] = DictionaryObject({NameObject("/ExtGState"): DictionaryObject(
        {NameObject("/CfHi"): writer._add_object(gs)})})
    stream = ContentStream(None, writer)
    stream.set_data("\n".join(ops).encode("ascii"))
    over.replace_contents(stream)
    page.merge_transformed_page(over, Transformation(geo.ctm()))


def _overlap(a, b) -> float:
    """How much two (left, bottom, right, top) boxes overlap, in square points."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _is_annot(name) -> bool:
    return isinstance(name, str) and name[:1] == "a" and name[1:].isdigit()


def _object_names(change: dict) -> set:
    return {n for n in set(change.get("remove", ())) | set(change.get("move", {})) | set(change.get("paint", {}))
            if not _is_annot(n)}


def _touches_objects(change: dict) -> bool:
    return bool(_object_names(change) or change.get("covers") or change.get("patches"))


def _move_points(arr, matrix):
    """An annotation's points (x, y, x, y, …), moved and resized like its box."""
    from pypdf.generic import ArrayObject, FloatObject
    out = ArrayObject()
    vals = [float(v) for v in arr]
    for k in range(0, len(vals) - 1, 2):
        x, y = _apply(matrix, vals[k], vals[k + 1])
        out += [FloatObject(round(x, 3)), FloatObject(round(y, 3))]
    return out


def _edit_annots(writer, page, change: dict):
    """The page's annotations as they were changed in the editor: moved (and resized), their words or link
    changed, deleted (with their pop-ups)."""
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
    annots = page.get("/Annots")
    if annots is None:
        return
    annots = annots.get_object()
    geo = pypdf_geo(page)
    for name, (old, new) in change.get("move", {}).items():
        if not _is_annot(name) or int(name[1:]) >= len(annots):
            continue
        a = annots[int(name[1:])].get_object()
        ol, ob, orr, ot = geo.rect_to_user(*old)
        nl, nb, nr, nt = geo.rect_to_user(*new)
        sx = (nr - nl) / (orr - ol) if orr - ol > 0.01 else 1.0
        sy = (nt - nb) / (ot - ob) if ot - ob > 0.01 else 1.0
        m = (sx, 0, 0, sy, nl - sx * ol, nb - sy * ob)
        if "/Rect" in a:
            a[NameObject("/Rect")] = _move_points(a["/Rect"], m)
        for key in ("/QuadPoints", "/Vertices", "/L", "/CL"):
            if key in a:
                a[NameObject(key)] = _move_points(a[key], m)
        if "/InkList" in a:
            a[NameObject("/InkList")] = ArrayObject(_move_points(s, m) for s in a["/InkList"])
        popup = a.get("/Popup")
        if popup is not None and "/Rect" in popup.get_object():  # (its pop-up goes along, not resized)
            p = popup.get_object()
            dx, dy = nl - ol, nt - ot
            p[NameObject("/Rect")] = ArrayObject(FloatObject(float(v) + (dx if k % 2 == 0 else dy))
                                                 for k, v in enumerate(p["/Rect"]))
    for name, look in change.get("paint", {}).items():
        if not _is_annot(name) or int(name[1:]) >= len(annots):
            continue
        a = annots[int(name[1:])].get_object()
        if "text" in look:
            a[NameObject("/Contents")] = TextStringObject(str(look["text"]))
            a[NameObject("/M")] = TextStringObject(_pdf_date())
            a.pop(NameObject("/RC"), None)  # (rich text with the old words)
            popup = a.get("/Popup")
            if popup is not None:
                popup.get_object().pop(NameObject("/Contents"), None)
        if a.get("/Subtype") == "/Link" and ("url" in look or "target" in look):
            url = web_address(str(look.get("url") or ""))
            target = look.get("target")
            if url:
                a[NameObject("/A")] = DictionaryObject({NameObject("/S"): NameObject("/URI"),
                                                        NameObject("/URI"): TextStringObject(url)})
                a.pop(NameObject("/Dest"), None)
            elif isinstance(target, int) and 0 <= target < len(writer.pages):
                a.pop(NameObject("/A"), None)
                a[NameObject("/Dest")] = ArrayObject([writer.pages[target].indirect_reference, NameObject("/Fit")])
    gone = sorted({int(n[1:]) for n in change.get("remove", ()) if _is_annot(n) and int(n[1:]) < len(annots)})
    if gone:
        drop = set()
        for k in gone:
            ref = annots[k]
            drop.add(id(ref))
            popup = ref.get_object().get("/Popup")
            if popup is not None:
                for other in annots:
                    if getattr(other, "idnum", None) == getattr(popup, "idnum", -1):
                        drop.add(id(other))
                        break
        page[NameObject("/Annots")] = ArrayObject(r for r in annots if id(r) not in drop)


def _pdf_date() -> str:
    import time
    return time.strftime("D:%Y%m%d%H%M%S", time.gmtime()) + "Z"


def _renamed(name, inlined: dict[int, int]) -> list:
    """A piece's names once the groups in inlined ({place: how many pieces in it}) are put into the page."""
    name = oid(name)
    if isinstance(name, str) and _is_annot(name):
        return [name]
    steps = [int(s) for s in str(name).split("/")]
    top = steps[0]
    shift = sum(n - 1 for k, n in inlined.items() if k < top)
    if top not in inlined:
        return [oid("/".join(map(str, [top + shift] + steps[1:])))]
    if len(steps) == 1:
        return [top + shift + j for j in range(inlined[top])]
    return [oid("/".join(map(str, [top + shift + steps[1]] + steps[2:])))]


def _rename_change(change: dict, inlined: dict[int, int]):
    change["remove"] = {new for n in change.get("remove", ()) for new in _renamed(n, inlined)}
    change["move"] = {new: v for n, v in change.get("move", {}).items() for new in _renamed(n, inlined)}
    change["paint"] = {new: v for n, v in change.get("paint", {}).items() for new in _renamed(n, inlined)}


def _kinds(page_raw) -> list[int]:
    import pypdfium2.raw as c
    return [c.FPDFPageObj_GetType(c.FPDFPage_GetObject(page_raw, k)) for k in range(c.FPDFPage_CountObjects(page_raw))]


def _groups_into_pages(data: bytes, changes: dict[int, dict]) -> bytes:
    """Groups (form XObjects) with a changed piece in them are put into their page, so PDFium doesn't write
    the whole group again (see pdfcontent). The changes' names are changed to match. A page where that can't
    be done keeps its groups: PDFium takes the piece out of its group instead."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    from pypdf import PdfReader, PdfWriter
    from . import pdfcontent
    failed: set[int] = set()
    for _round in range(6):
        wanted = {}
        for i, ch in changes.items():
            tops = {int(str(n).split("/")[0]) for n in _object_names(ch) if isinstance(n, str)}
            if tops and i not in failed:
                wanted[i] = tops
        if not wanted:
            break
        plans = {}
        with PDFIUM:
            doc = pdfium.PdfDocument(data)
            try:
                for i, tops in wanted.items():
                    if not 0 <= i < len(doc):
                        failed.add(i)
                        continue
                    page = doc[i]
                    try:
                        kinds = _kinds(page.raw)
                        inlined, ordinals, expect = {}, set(), []
                        for k, kind in enumerate(kinds):
                            if k in tops and kind == c.FPDF_PAGEOBJ_FORM:
                                o = c.FPDFPage_GetObject(page.raw, k)
                                kids = [c.FPDFPageObj_GetType(c.FPDFFormObj_GetObject(o, j))
                                        for j in range(c.FPDFFormObj_CountObjects(o))]
                                inlined[k] = len(kids)
                                ordinals.add(sum(1 for x in kinds[:k] if x == c.FPDF_PAGEOBJ_FORM))
                                expect += kids
                            else:
                                expect.append(kind)
                        if inlined and all(x in inlined for x in tops):
                            plans[i] = (inlined, ordinals, expect)
                        else:
                            failed.add(i)
                    finally:
                        page.close()
            finally:
                doc.close()
        while plans:
            writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
            for i, (_inlined, ordinals, _expect) in list(plans.items()):
                if not pdfcontent.inline_groups(writer, writer.pages[i], ordinals):
                    failed.add(i)
                    del plans[i]
            if not plans:
                break
            buf = io.BytesIO()
            writer.write(buf)
            bad = set()
            with PDFIUM:
                doc = pdfium.PdfDocument(buf.getvalue())
                try:
                    for i, (_inlined, _ordinals, expect) in plans.items():
                        page = doc[i]
                        try:
                            if _kinds(page.raw) != expect:
                                bad.add(i)
                        finally:
                            page.close()
                finally:
                    doc.close()
            if bad:  # (PDFium reads the page another way than expected: those pages are left as they were)
                failed |= bad
                for i in bad:
                    del plans[i]
                continue
            data = buf.getvalue()
            for i, (inlined, _ordinals, _expect) in plans.items():
                _rename_change(changes[i], inlined)
            break
    return data


def _prepare(src: Path, password: str, changes: dict[int, dict]) -> bytes:
    """The PDF (open, as bytes) ready for PDFium: annotations changed, groups with a changed piece put into
    their page, and each page with pieces to change cut into small streams (see pdfcontent)."""
    from pypdf import PdfReader, PdfWriter
    from . import pdfcontent
    writer = PdfWriter(clone_from=open_reader(src, password))
    for i, ch in changes.items():
        if 0 <= i < len(writer.pages):
            _edit_annots(writer, writer.pages[i], ch)
    buf = io.BytesIO()
    writer.write(buf)
    data = _groups_into_pages(buf.getvalue(), changes)
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    cut = False
    for i, ch in changes.items():
        if 0 <= i < len(writer.pages) and _touches_objects(ch):
            cut = pdfcontent.cut_page(writer, writer.pages[i]) or cut
    if not cut:
        return data
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _lift(c, page_raw, o, parent, m, top):
    """A piece inside a group, taken out of it and put on the page just after the group (top), drawn the
    same. (Only when the group couldn't be put into the page.)"""
    if not c.FPDFFormObj_RemoveObject(parent, o):
        return False
    c.FPDFPageObj_SetMatrix(o, c.FS_MATRIX(*_mul(_mx(o), m)))
    at = next((k for k in range(c.FPDFPage_CountObjects(page_raw)) if _addr(c.FPDFPage_GetObject(page_raw, k)) ==
               _addr(top)), None)
    if at is None:
        c.FPDFPage_InsertObject(page_raw, o)
    else:
        c.FPDFPage_InsertObjectAtIndex(page_raw, o, at + 1)
    return True


def _take_out(data: bytes, changes: dict[int, dict]) -> bytes:
    """With PDFium: the old pieces that were changed or deleted are taken out of their pages, the moved ones
    are moved and shapes get their new colours. What's under a cover is taken out of the file, not just
    hidden: pieces fully under it go, and a piece partly under it (a line with one number covered, a scanned
    page) becomes a picture of itself without the covered part. data: the PDF as _prepare made it (open).
    Gives the PDF as bytes."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    with PDFIUM:
        doc = pdfium.PdfDocument(data)
        alone = None  # (a second copy, to draw one piece on its own)
        try:
            for index, change in changes.items():
                if not 0 <= index < len(doc) or not _touches_objects(change):
                    continue  # (a blank page added in the editor, or only its annotations changed)
                page = doc[index]
                try:
                    geo = page_geo(page)
                    found = {n: find_object(page.raw, n) for n in _object_names(change)}
                    # pieces left inside a group (it couldn't be put into the page, see _groups_into_pages): the
                    # moved and recoloured ones are taken out of it onto the page, and the group becomes a picture
                    # of itself without them (PDFium can't write a group again)
                    by_group: dict[int, list] = {}
                    for n, (o, parent, _m) in found.items():
                        if o is not None and parent is not None:
                            by_group.setdefault(int(str(n).split("/")[0]), []).append(n)
                    groups = {k: c.FPDFPage_GetObject(page.raw, k) for k in by_group}
                    for k, names in by_group.items():
                        if alone is None:
                            alone = pdfium.PdfDocument(data)
                        top = groups[k]
                        pic = _group_picture(alone, index, k, names, geo)
                        for n in names:
                            o, parent, m = found[n]
                            if n in change.get("remove", ()) or not _lift(c, page.raw, o, parent, m, top):
                                found[n] = (None, None, None)
                            else:
                                found[n] = (o, None, _ID)
                        at = next((j for j in range(c.FPDFPage_CountObjects(page.raw))
                                   if _addr(c.FPDFPage_GetObject(page.raw, j)) == _addr(top)), None)
                        if at is None:
                            continue
                        if pic is not None:
                            img = c.FPDFPageObj_NewImageObj(doc.raw)
                            c.FPDFImageObj_SetBitmap(None, 0, img, pic[0].raw)
                            c.FPDFImageObj_SetMatrix(img, *pic[1])
                            c.FPDFPage_InsertObjectAtIndex(page.raw, img, at)
                        if c.FPDFPage_RemoveObject(page.raw, top):
                            c.FPDFPageObj_Destroy(top)
                    count = c.FPDFPage_CountObjects(page.raw)
                    objs = [c.FPDFPage_GetObject(page.raw, k) for k in range(count)]
                    place = {_addr(o): k for k, o in enumerate(objs)}
                    gone = {place[_addr(found[n][0])] for n in change.get("remove", ())
                            if n in found and found[n][0] is not None and _addr(found[n][0]) in place}
                    for n, look in change.get("paint", {}).items():
                        o = found.get(n, (None,))[0]
                        if o is not None:
                            _paint(c, o, look)
                    matrices = {}
                    for n, (old, new) in change.get("move", {}).items():
                        o = found.get(n, (None,))[0]
                        if o is None or _addr(o) not in place or place[_addr(o)] in gone:
                            continue
                        k = place[_addr(o)]
                        ol, ob, orr, ot = geo.rect_to_user(*old)
                        nl, nb, nr, nt = geo.rect_to_user(*new)
                        sx = (nr - nl) / (orr - ol) if orr - ol > 0.01 else 1.0
                        sy = (nt - nb) / (ot - ob) if ot - ob > 0.01 else 1.0
                        matrices[k] = (sx, 0, 0, sy, nl - sx * ol, nb - sy * ob)
                        c.FPDFPageObj_Transform(o, *matrices[k])
                    shown_covers = list(change.get("covers", ())) + list(change.get("patches", ()))
                    covers = [geo.rect_to_user(*r) for r in shown_covers]
                    partly = []
                    for k, o in enumerate(objs):
                        if k in gone or not covers:
                            continue
                        box = _bounds(o)
                        if any(box[0] >= l - 0.5 and box[1] >= b - 0.5 and box[2] <= r + 0.5 and box[3] <= t + 0.5
                               for l, b, r, t in covers):
                            gone.add(k)
                        elif any(_overlap(box, cv) > 0.01 for cv in covers):
                            partly.append(k)
                    pictures = {}
                    if partly:
                        if alone is None:
                            alone = pdfium.PdfDocument(data)
                        lettered = _letters_under(page, objs, covers)
                        for k in partly:
                            pic = _without_covered(alone, index, k, matrices, geo, shown_covers, objs[k])
                            if pic is not None:
                                pictures[k] = pic
                            elif k in lettered:
                                # letters under the cover that don't show (a scan's hidden text, made searchable;
                                # see-through or cut-off words): they'd still be found and copied, so they go
                                gone.add(k)
                    for k in sorted(pictures, reverse=True):  # (from the top down: the places below stay put)
                        img = c.FPDFPageObj_NewImageObj(doc.raw)
                        bitmap, matrix = pictures[k]
                        c.FPDFImageObj_SetBitmap(None, 0, img, bitmap.raw)
                        c.FPDFImageObj_SetMatrix(img, *matrix)
                        c.FPDFPage_InsertObjectAtIndex(page.raw, img, k)
                        gone.add(k)
                    for k in sorted(gone, reverse=True):
                        o = objs[k]
                        if o and c.FPDFPage_RemoveObject(page.raw, o):
                            c.FPDFPageObj_Destroy(o)
                    real = [geo.rect_to_user(*r) for r in change.get("covers", ())]
                    for n in reversed(range(c.FPDFPage_GetAnnotCount(page.raw)) if real else ()):
                        annot = c.FPDFPage_GetAnnot(page.raw, n)  # (notes, stamps, form boxes under a cover)
                        try:
                            rect = c.FS_RECTF()
                            hit = c.FPDFAnnot_GetRect(annot, rect) and \
                                c.FPDFAnnot_GetSubtype(annot) not in (c.FPDF_ANNOT_POPUP, c.FPDF_ANNOT_LINK) and \
                                any(_overlap((min(rect.left, rect.right), min(rect.bottom, rect.top),
                                              max(rect.left, rect.right), max(rect.bottom, rect.top)), cv) > 0.01
                                    for cv in real)
                        finally:
                            c.FPDFPage_CloseAnnot(annot)
                        if hit:
                            c.FPDFPage_RemoveAnnot(page.raw, n)
                    if not c.FPDFPage_GenerateContent(page.raw):
                        raise RuntimeError(tr("page {number} couldn't be changed", number=index + 1))
                finally:
                    page.close()
            out = io.BytesIO()
            doc.save(out, flags=c.FPDF_NO_INCREMENTAL)
            return out.getvalue()
        finally:
            if alone is not None:
                alone.close()
            doc.close()


def _group_picture(alone, index: int, k: int, without: list, geo: "Geo"):
    """A picture of the page's group k (a form XObject) on its own, without some of its pieces (their names),
    and where it goes (an image matrix), or None when nothing of it shows. Call with PDFIUM held."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    page = alone[index]
    try:
        found = [find_object(page.raw, n) for n in without]
        for o, parent, _m in found:
            if o is not None and parent is not None:
                c.FPDFFormObj_RemoveObject(parent, o)  # (in memory only: the page isn't saved)
        mine = c.FPDFPage_GetObject(page.raw, k)
        for o in [c.FPDFPage_GetObject(page.raw, n) for n in range(c.FPDFPage_CountObjects(page.raw)) if n != k]:
            c.FPDFPage_RemoveObject(page.raw, o)
            c.FPDFPageObj_Destroy(o)
        l, b, r, t = _bounds(mine)
        corners = [geo.to_display(x, y) for x, y in ((l, b), (r, b), (l, t), (r, t))]
        x0, y0 = max(0.0, min(p[0] for p in corners)), max(0.0, min(p[1] for p in corners))
        x1, y1 = min(geo.width, max(p[0] for p in corners)), min(geo.height, max(p[1] for p in corners))
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            return None
        scale = min(300 / 72, math.sqrt(40e6 / max(1.0, (x1 - x0) * (y1 - y0))))
        bitmap = page.render(scale=scale, crop=(x0, geo.height - y1, geo.width - x1, y0), fill_color=(0, 0, 0, 0),
                             may_draw_forms=False)
        img = bitmap.to_pil().convert("RGBA")
        if img.getchannel("A").getbbox() is None:
            return None
        out = pdfium.PdfBitmap.from_pil(img if img.getchannel("A").getextrema()[0] < 255 else img.convert("RGB"))
        bl, br, tl = geo.to_user(x0, y1), geo.to_user(x1, y1), geo.to_user(x0, y0)
        return out, (br[0] - bl[0], br[1] - bl[1], tl[0] - bl[0], tl[1] - bl[1], bl[0], bl[1])
    finally:
        page.close()


def _letters_under(page, objs: list, covers) -> set[int]:
    """The page's pieces (their places in objs) with at least one letter under a cover, whether it shows or not.
    covers: in the page's own points. Call with PDFIUM held."""
    import pypdfium2.raw as c
    where = {_addr(o): k for k, o in enumerate(objs) if o}
    forms = [(k, _bounds(o)) for k, o in enumerate(objs) if o and c.FPDFPageObj_GetType(o) == c.FPDF_PAGEOBJ_FORM]
    found: set[int] = set()
    tp = c.FPDFText_LoadPage(page.raw)
    if not tp:
        return found
    try:
        l, r, b, t = (ctypes.c_double() for _ in range(4))
        for i in range(max(0, c.FPDFText_CountChars(tp))):
            if not c.FPDFText_GetCharBox(tp, i, l, r, b, t):
                continue
            box = (min(l.value, r.value), min(b.value, t.value), max(l.value, r.value), max(b.value, t.value))
            if box[2] - box[0] <= 0 or box[3] - box[1] <= 0 or not any(_overlap(box, cv) > 0 for cv in covers):
                continue
            obj = c.FPDFText_GetTextObject(tp, i) if hasattr(c, "FPDFText_GetTextObject") else None
            k = where.get(_addr(obj)) if obj else None
            if k is not None:
                found.add(k)
            else:  # (a letter inside a group of pieces, or an older PDFium: the groups and texts around it)
                found.update(n for n, fb in forms if _overlap(box, fb) > 0)
                if obj is None:
                    found.update(n for n, o in enumerate(objs) if o and c.FPDFPageObj_GetType(o) == c.FPDF_PAGEOBJ_TEXT
                                 and _overlap(box, _bounds(o)) > 0)
    finally:
        c.FPDFText_ClosePage(tp)
    return found


def _without_covered(alone, index: int, k: int, matrices: dict, geo: "Geo", covers, obj):
    """A picture of the page's piece k on its own, with the covered parts cut out (see-through), and
    where it goes (an image matrix), or None when nothing of it is under a cover. Call with PDFIUM held."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    page = alone[index]
    try:
        count = c.FPDFPage_CountObjects(page.raw)
        mine = c.FPDFPage_GetObject(page.raw, k)
        if k in matrices:
            c.FPDFPageObj_Transform(mine, *matrices[k])
        for o in [c.FPDFPage_GetObject(page.raw, n) for n in range(count) if n != k]:
            c.FPDFPage_RemoveObject(page.raw, o)  # (in memory only: the page isn't saved)
            c.FPDFPageObj_Destroy(o)
        # where the piece is, as the page is shown
        l, b, r, t = _bounds(obj)
        corners = [geo.to_display(x, y) for x, y in ((l, b), (r, b), (l, t), (r, t))]
        x0, y0 = max(0.0, min(p[0] for p in corners)), max(0.0, min(p[1] for p in corners))
        x1, y1 = min(geo.width, max(p[0] for p in corners)), min(geo.height, max(p[1] for p in corners))
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            return None
        scale = 300 / 72
        if c.FPDFPageObj_GetType(obj) == c.FPDF_PAGEOBJ_IMAGE:  # (a scan keeps about its own sharpness)
            pw, ph = ctypes.c_uint(), ctypes.c_uint()
            if c.FPDFImageObj_GetImagePixelSize(obj, pw, ph):
                scale = min(400 / 72, max(100 / 72, max(pw.value, ph.value) / max(x1 - x0, y1 - y0, 1)))
        scale = min(scale, math.sqrt(40e6 / max(1.0, (x1 - x0) * (y1 - y0))))
        bitmap = page.render(scale=scale, crop=(x0, geo.height - y1, geo.width - x1, y0), fill_color=(0, 0, 0, 0),
                             may_draw_forms=False)
        img = bitmap.to_pil().convert("RGBA")
        alpha = img.getchannel("A")
        hidden = False
        for x, y, w, h in covers:
            box = (round((x - x0) * scale), round((y - y0) * scale), round((x + w - x0) * scale),
                   round((y + h - y0) * scale))
            box = (max(0, box[0]), max(0, box[1]), min(img.width, box[2]), min(img.height, box[3]))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            if alpha.crop(box).getbbox() is not None:
                hidden = True
            img.paste((0, 0, 0, 0), box)
        if not hidden:
            return None  # (nothing of it shows under the covers: it can stay as it is)
        out = pdfium.PdfBitmap.from_pil(img if img.getchannel("A").getextrema()[0] < 255 else img.convert("RGB"))
        # the picture's corners on the page (images are drawn into a 1 × 1 square)
        bl, br, tl = geo.to_user(x0, y1), geo.to_user(x1, y1), geo.to_user(x0, y0)
        matrix = (br[0] - bl[0], br[1] - bl[1], tl[0] - bl[0], tl[1] - bl[1], bl[0], bl[1])
        return out, matrix
    finally:
        page.close()


def _drop_lost_fields(reader):
    """Form boxes a cover took off the page: their fields (and what was typed in them) go too, before the
    copy is made (the copy only takes what's still used)."""
    from pypdf.generic import ArrayObject, IndirectObject, NameObject
    root = reader.trailer["/Root"]
    form = root.get("/AcroForm")
    if form is None:
        return
    form = form.get_object()
    on_pages = {ref.idnum for page in reader.pages for ref in (page.get("/Annots") or [])
                if isinstance(ref, IndirectObject)}

    def keep(ref) -> bool:
        field = ref.get_object()
        kids = field.get("/Kids")
        if kids is not None:
            left = ArrayObject(k for k in kids if keep(k))
            if not left:
                return False
            field[NameObject("/Kids")] = left
            return True
        return field.get("/Subtype") != "/Widget" or (isinstance(ref, IndirectObject) and ref.idnum in on_pages)
    fields = form.get("/Fields")
    if fields is not None:
        form[NameObject("/Fields")] = ArrayObject(f for f in fields.get_object() if keep(f))


def _forget_old_looks(reader, covered: set[int]):
    """A covered page's small picture of itself (/Thumb) and what the program that made it kept of the page
    for itself (/PieceInfo: Illustrator keeps the whole page there) would still show what's under the cover:
    they go, before the copy is made (the copy only takes what's still used)."""
    from pypdf.generic import NameObject
    for i in covered:
        if 0 <= i < len(reader.pages):
            page = reader.pages[i]
            for one in (page, page.indirect_reference.get_object() if page.indirect_reference else None):
                for key in ("/Thumb", "/PieceInfo"):  # (pypdf's page is a copy of the one in the file)
                    if one is not None:
                        one.pop(NameObject(key), None)
    reader.trailer["/Root"].get_object().pop(NameObject("/PieceInfo"), None)


def _field_picture(writer, w: float, h: float, field: dict, value: str, turn: int = 0):
    """A text box's look with its new value, made by Qt (Arabic is joined, every PDF app shows it). turn: the
    box's own turn on the page (its /MK /R, counterclockwise)."""
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QRectF, QSizeF, Qt
    from PySide6.QtGui import QColor, QPageLayout, QPageSize, QPainter, QPdfWriter
    turn = turn % 360
    if turn in (90, 270):  # (a box turned on its side: the words run along its long side)
        w, h = h, w
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    pw = QPdfWriter(buf)
    pw.setResolution(72)
    pw.setPageLayout(QPageLayout(QPageSize(QSizeF(max(w, 1), max(h, 1)), QPageSize.Point, "", QPageSize.ExactMatch),
                                 QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    p = QPainter(pw)
    size = field.get("size") or min(12.0, max(6.0, h * 0.66))
    f = font_for({"font": "Arial", "size": size})
    p.setFont(f)
    p.setPen(QColor("#000000"))
    if field.get("multiline"):
        flags = Qt.AlignTop | Qt.TextWordWrap
    else:
        flags = Qt.AlignVCenter
    rtl = _strong(value) == "R"
    flags |= (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
    p.drawText(QRectF(2, 1, max(w - 4, 1), max(h - 2, 1)), int(flags), value)
    p.end()
    buf.close()
    page = PdfReader(io.BytesIO(_arabic_unicode(bytes(data), value))).pages[0]
    xo = DecodedStreamObject()
    xo.set_data(page.get_contents().get_data() if page.get_contents() is not None else b"")
    xo.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
               NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(w), FloatObject(h)]),
               NameObject("/Resources"): page["/Resources"].clone(writer)})
    if turn in (90, 180, 270):
        cos, sin = {90: (0, 1), 180: (-1, 0), 270: (0, -1)}[turn]
        xo[NameObject("/Matrix")] = ArrayObject([FloatObject(v) for v in (cos, sin, -sin, cos, 0, 0)])
    return writer._add_object(xo)


def _fill(writer, values: dict, fields: list[dict]):
    """Puts the typed values in the form's boxes."""
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject
    if not values:
        return
    kinds = {f["name"]: f for f in fields}
    for page in writer.pages:
        for ref in page.get("/Annots") or []:
            w = ref.get_object()
            if w.get("/Subtype") != "/Widget":
                continue
            name = _full_name(w)
            if name not in values or name not in kinds:
                continue
            field, value = kinds[name], values[name]
            holder = w if "/T" in w else (w["/Parent"].get_object() if "/Parent" in w else w)
            if field["kind"] in ("text", "choice"):
                stored = str(value)
                if field["kind"] == "choice" and stored in field.get("options", []):
                    stored = field.get("exports", field["options"])[field["options"].index(stored)]
                holder[NameObject("/V")] = TextStringObject(stored)
                l, b, r, t = (float(v) for v in w["/Rect"])
                try:  # (a box on a turned page is turned too, so its words show upright)
                    mk = w.get("/MK")
                    turn = int(mk.get_object().get("/R", 0)) if mk is not None else 0
                except (TypeError, ValueError, AttributeError):
                    turn = 0
                pic = _field_picture(writer, abs(r - l), abs(t - b), field, str(value), turn)
                w[NameObject("/AP")] = DictionaryObject({NameObject("/N"): pic})
            elif field["kind"] in ("check", "radio"):
                state = str(value) if value and value != "/Off" else "/Off"
                holder[NameObject("/V")] = NameObject(state)
                mine = _on_state(w)
                w[NameObject("/AS")] = NameObject(mine if state == mine else "/Off")
    form = writer._root_object.get("/AcroForm")
    if form is not None:
        form.get_object()[NameObject("/NeedAppearances")] = __import__("pypdf").generic.BooleanObject(False)


_INHERITED = ("/Resources", "/MediaBox", "/CropBox", "/Rotate")


def _forget_pages(writer, gone: set[int]):
    """Bookmarks, links and form boxes that still point at a deleted page point nowhere now, so the page (and
    what's on it) isn't kept in the new copy."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NullObject
    seen: set[int] = set()
    todo = [writer._root_object]
    while todo:
        obj = todo.pop()
        for key, value in list(obj.items() if isinstance(obj, DictionaryObject) else enumerate(obj)):
            if isinstance(value, IndirectObject):
                if value.idnum in gone:
                    obj[key] = NullObject()
                    continue
                if value.idnum in seen:
                    continue
                seen.add(value.idnum)
                value = value.get_object()
            if isinstance(value, (DictionaryObject, ArrayObject)):
                todo.append(value)


def _arrange(writer, order: list[int] | None, turns: dict[int, int]) -> set[int]:
    """Turns pages, and keeps only the pages in order, in that order (the editor's list of pages). Gives the
    deleted pages (their object numbers)."""
    from pypdf.generic import ArrayObject, NameObject, NumberObject
    pages = list(writer.pages)
    moved = order is not None and order != list(range(len(pages)))
    if not moved and not turns:
        return set()
    for page in pages:  # (what a page took from the page groups it's in, it keeps itself)
        for key in _INHERITED:
            if key in page:
                continue
            node = page.get("/Parent")
            while node is not None:
                node = node.get_object()
                if key in node:
                    page[NameObject(key)] = node[key]
                    break
                node = node.get("/Parent")
    for i, degrees in turns.items():
        if 0 <= i < len(pages):
            pages[i].rotate(degrees % 360)
    if not moved:
        return set()
    keep = [pages[i] for i in order if 0 <= i < len(pages)]
    if not keep:
        raise RuntimeError(tr("a PDF needs at least one page"))
    root = writer._root_object["/Pages"]
    for page in keep:
        page[NameObject("/Parent")] = root.indirect_reference
    root[NameObject("/Kids")] = ArrayObject([p.indirect_reference for p in keep])
    root[NameObject("/Count")] = NumberObject(len(keep))
    writer.flattened_pages = None  # (pypdf's own list of the pages: made again when it's needed)
    kept = {p.indirect_reference.idnum for p in keep}
    gone = {p.indirect_reference.idnum for p in pages if p.indirect_reference.idnum not in kept}
    if gone:
        _forget_pages(writer, gone)
    return gone


def web_address(text: str) -> str:
    """What was typed for a link, as a web or mail address a PDF app opens ("" when it isn't one: no other
    kinds of links, like ones that start a program, are made)."""
    text = (text or "").strip()
    if not text or any(ch.isspace() or ord(ch) < 32 for ch in text):
        return ""
    low = text.lower()
    if low.startswith(("http://", "https://", "mailto:")):
        return text if len(text) > len(low.split(":", 1)[0]) + 3 else ""
    if "@" in text and "/" not in text and "." in text.split("@", 1)[1]:
        return "mailto:" + text
    if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", text) and not low.endswith((".exe", ".bat", ".cmd", ".js", ".vbs")):
        return "https://" + text
    return ""


def _links(writer, edits: list[dict]):
    """Links added in the editor: to a web or mail address, or to one of the PDF's pages."""
    from pypdf.annotations import Link
    pages = writer.pages
    for d in edits:
        if d.get("kind") != "link":
            continue
        i = int(d.get("page", 0))
        if not 0 <= i < len(pages):
            continue
        url = web_address(str(d.get("url", "")))
        target = d.get("target")
        if not url and not (isinstance(target, int) and 0 <= target < len(pages)):
            continue
        geo = pypdf_geo(pages[i])
        rect = geo.rect_to_user(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]))
        link = Link(rect=rect, border=[0, 0, 0], url=url) if url else \
            Link(rect=rect, border=[0, 0, 0], target_page_index=target)
        writer.add_annotation(page_number=i, annotation=link)


def _changes(edits: list[dict]) -> dict[int, dict]:
    """What's changed of what was already on each page: {page: {"remove": names, "move": {name: (was, now)},
    "paint": {name: look}, "covers": boxes, "patches": boxes}} (names: see oid; annotations too)."""
    changes: dict[int, dict] = {}
    for d in edits:
        page = int(d.get("page", 0))
        if d.get("replaces"):
            changes.setdefault(page, {}).setdefault("remove", set()).update(oid(k) for k in d["replaces"])
        kind = d.get("kind")
        if kind == "remove":
            changes.setdefault(page, {}).setdefault("remove", set()).update(oid(k) for k in d.get("objs", []))
        elif kind == "move":
            for k in d.get("objs", []):
                changes.setdefault(page, {}).setdefault("move", {})[oid(k)] = (d["orig"], [d["x"], d["y"], d["w"],
                                                                                          d["h"]])
        elif kind == "cover":
            changes.setdefault(page, {}).setdefault("covers", []).append((d["x"], d["y"], d["w"], d["h"]))
        elif kind == "restyle":
            look = {k: d[k] for k in ("color", "fill", "text", "url", "target") if k in d}
            for k in d.get("objs", []):
                changes.setdefault(page, {}).setdefault("paint", {})[oid(k)] = look
        if kind == "text" and d.get("patch"):
            x, y, w, h = (float(v) for v in d["patch"][:4])
            changes.setdefault(page, {}).setdefault("patches", []).append((x, y, w, h))
    return changes


def save_edits(src: Path, ctx: Ctx) -> Result:
    """ctx.params: edits (the editor's list of dicts), fields ({name: value}), password; order (the pages kept,
    in their new order) and turns ({page: degrees clockwise}), when the pages were changed; blanks (the sizes
    of blank pages added: they come after the PDF's own pages, so the first is page len(pages) in edits and
    order)."""
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.annotations import Text
    edits: list[dict] = list(ctx.params.get("edits") or [])
    values: dict = dict(ctx.params.get("fields") or {})
    password = ctx.params.get("password", "") or ""
    order = ctx.params.get("order")
    turns = {int(k): int(v) for k, v in (ctx.params.get("turns") or {}).items() if int(v) % 360}
    blanks = [(float(w), float(h)) for w, h in (ctx.params.get("blanks") or [])]
    if not edits and not values and order is None and not turns and not blanks:
        raise RuntimeError(tr("nothing was changed"))
    # 1. old pieces out (or moved, or given other colours), annotations changed
    changes = _changes(edits)
    ctx.progress(0.05)
    if changes:
        reader = PdfReader(io.BytesIO(_take_out(_prepare(src, password, changes), changes)))
        if any(ch.get("covers") or ch.get("patches") for ch in changes.values()):
            _drop_lost_fields(reader)
            _forget_old_looks(reader, {i for i, ch in changes.items() if ch.get("covers") or ch.get("patches")})
    else:
        reader = open_reader(src, password)
    fields = read_fields(reader) if values else []
    ctx.check()
    ctx.progress(0.3)
    writer = PdfWriter(clone_from=reader)
    for w, h in blanks:
        writer.add_blank_page(width=max(36.0, w), height=max(36.0, h))
    # 2. highlights, then everything drawn on top
    pages = writer.pages
    drawn: dict[int, list[dict]] = {}
    for d in edits:
        i = int(d.get("page", 0))
        if not 0 <= i < len(pages):
            continue
        if d.get("kind") == "highlight":
            continue
        if d.get("kind") in DRAWN and not (d.get("kind") == "text" and not str(d.get("text", "")).strip()):
            drawn.setdefault(i, []).append(d)
    for i in sorted({int(d.get("page", 0)) for d in edits if d.get("kind") == "highlight"}):
        if 0 <= i < len(pages):
            _highlights(writer, pages[i], pypdf_geo(pages[i]),
                        [d for d in edits if d.get("kind") == "highlight" and int(d.get("page", 0)) == i])
    ctx.check()
    if drawn:
        geos = {i: pypdf_geo(pages[i]) for i in drawn}
        layer = PdfReader(io.BytesIO(_layer(drawn, {i: (g.width, g.height) for i, g in geos.items()})))
        for n, i in enumerate(drawn):
            # (the layer's page is a whole number of points tall: its top is lined up with the page's top)
            a, b, c, d, e, f = geos[i].ctm()
            dy = geos[i].height - float(layer.pages[n].mediabox.height)
            pages[i].merge_transformed_page(layer.pages[n], Transformation((a, b, c, d, e + c * dy, f + d * dy)))
            ctx.progress(0.4 + 0.4 * (n + 1) / len(drawn))
    # 3. notes and the form
    for d in edits:
        if d.get("kind") != "note" or not str(d.get("text", "")).strip():
            continue
        i = int(d.get("page", 0))
        if not 0 <= i < len(pages):
            continue
        geo = pypdf_geo(pages[i])
        l, b, r, t = geo.rect_to_user(float(d["x"]), float(d["y"]), 20, 20)
        note = Text(rect=(l, b, r, t), text=str(d["text"]), open=False)
        writer.add_annotation(page_number=i, annotation=note)
    _links(writer, edits)
    _fill(writer, values, fields)
    if _arrange(writer, order, turns):
        # (pypdf writes every part it was given: copied again, only what the pages still use is kept, so a
        # deleted page is really gone from the file, with its form boxes)
        buf = io.BytesIO()
        writer.write(buf)
        again = PdfReader(buf)
        _drop_lost_fields(again)
        writer = PdfWriter(clone_from=again)
    if password:
        writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    ctx.progress(0.9)
    out = ctx.out_path(src, f"{ctx.stem(src)} (edited).pdf")
    write(writer, out)
    return Result([out])


EDIT = Tool("pdftool:edit", tr("Edit"), tr("Save the edited PDF"), save_edits, renames=False)
