"""Corefold PDF's page editor, the part with no window: reading what is on a page (its lines of text,
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
import unicodedata
from pathlib import Path

from .core import Ctx, Result, Tool
from .i18n import tr
from .pdftools import PDFIUM, open_reader, write

log = logging.getLogger("corefold")

# the fonts offered for new text (those not on this PC are left out of the list)
FONTS = ("Arial", "Calibri", "Segoe UI", "Times New Roman", "Georgia", "Cambria", "Courier New", "Tahoma",
         "Verdana", "Sakkal Majalla", "Traditional Arabic")
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


def line_text(chars: list[tuple[str, float, float]], size: float) -> tuple[str, bool]:
    """The words of one line in reading order, from its letters as the PDF has them ((letter, left,
    right) in the PDF's order). Words are found by the gaps between letters, and put in order by where
    they are (many PDFs keep Arabic in the order it's shown): right to left on an Arabic line, with
    English in it kept in its own order. A word kept backwards is turned around."""
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
    made = [(text_of(w), (w[0][1] + w[-1][2]) / 2) for w in words]
    rtl = _strong("".join(t for t, _ in made)) == "R"
    made.sort(key=lambda t: -t[1] if rtl else t[1])
    # words written the other way (English in Arabic, Arabic in English) keep their own order
    other = "L" if rtl else "R"
    out, i = [], 0
    while i < len(made):
        j = i
        while j < len(made) and _strong(made[j][0]) == other:
            j += 1
        if j - i > 1:
            out += [t for t, _ in reversed(made[i:j])]
            i = j
        else:
            out.append(made[i][0])
            i += 1
    return " ".join(out), rtl


def read_layout(doc, index: int) -> dict:
    """What can be changed on a page: its lines of text and pictures, with where they are, and every
    letter's box (to highlight words). Call with PDFIUM held."""
    import pypdfium2.raw as c
    page = doc[index]
    try:
        geo = page_geo(page)
        top: dict[int, int] = {}
        texts: dict[int, dict] = {}
        images = []
        for k in range(c.FPDFPage_CountObjects(page.raw)):
            o = c.FPDFPage_GetObject(page.raw, k)
            kind = c.FPDFPageObj_GetType(o)
            top[_addr(o)] = k
            if kind == c.FPDF_PAGEOBJ_IMAGE:
                rect = geo.rect_to_display(*_bounds(o))
                if rect[2] > 2 and rect[3] > 2:
                    images.append({"obj": k, "rect": rect})
            elif kind == c.FPDF_PAGEOBJ_TEXT:
                texts[k] = {"raw": o}
        tp = page.get_textpage()
        try:
            n = c.FPDFText_CountChars(tp.raw)
            chars = []      # (letter, object, box in the PDF's points)
            box = c.FS_RECTF()
            for i in range(n):
                ch = chr(c.FPDFText_GetUnicode(tp.raw, i) or 32)
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
        marks = []
        for ch, gen, obj, rect in chars:
            if rect is None or gen or ch in "\r\n":
                continue
            x, y, w, h = geo.rect_to_display(*rect)
            if w > 0 and h > 0:
                marks.append((round(x, 2), round(y, 2), round(w, 2), round(h, 2)))
        return {"size": (geo.width, geo.height), "lines": lines, "images": images, "chars": marks}
    finally:
        page.close()


def _lines(c, geo: Geo, chars: list, texts: dict) -> list[dict]:
    """Letters -> lines that can be changed: letters next to each other on a line, from text that is on
    the page itself (not inside a drawing of its own), written left to right as shown."""
    info: dict[int, dict] = {}

    def obj_info(k: int) -> dict | None:
        if k in info:
            return info[k]
        raw = texts[k]["raw"]
        m = c.FS_MATRIX()
        c.FPDFPageObj_GetMatrix(raw, m)
        size = ctypes.c_float(0)
        c.FPDFTextObj_GetFontSize(raw, size)
        scale = math.sqrt(abs(m.a * m.d - m.b * m.c)) or 1
        # the way the line runs, as shown: only straight lines left to right can be edited
        p0, p1 = geo.to_display(m.e, m.f), geo.to_display(m.e + m.a, m.f + m.b)
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        straight = dx > 0 and abs(dy) < 0.02 * dx
        family, flags, weight, angle = _font_name(c.FPDFTextObj_GetFont(raw))
        name, bold, italic = clean_family(family)
        r, g, b, a = (ctypes.c_uint() for _ in range(4))
        c.FPDFPageObj_GetFillColor(raw, r, g, b, a)
        info[k] = {"size": size.value * scale, "straight": straight, "font": name,
                   "serif": bool(flags & 2), "fixed": bool(flags & 1),
                   "bold": bold or weight >= 600 or bool(flags & (1 << 18)), "italic": italic or angle != 0,
                   "color": f"#{r.value:02x}{g.value:02x}{b.value:02x}", "bounds": _bounds(raw),
                   "base": p0[1]}
        return info[k]

    # letters into runs: the letters of one line (as PDFium reads the page), split where there's a big
    # gap (a table's columns) or the size changes
    runs: list[list] = []
    line: list = []

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
        if o["straight"] and o["size"] > 0.5:
            x, y, w, h = geo.rect_to_display(*rect)
            line.append((ch, obj, (x, y, x + w, y + h), n))
    end_line()

    # one PDF text piece belongs to one line only (it's taken out whole)
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner: dict[int, int] = {}
    for i, run in enumerate(runs):
        for _, obj, _, _ in run:
            if obj in owner:
                parent[find(i)] = find(owner[obj])
            else:
                owner[obj] = i
    groups: dict[int, list] = {}
    for i, run in enumerate(runs):
        groups.setdefault(find(i), []).append(i)
    lines = []
    for members in groups.values():
        letters = sorted((x for i in members for x in runs[i]), key=lambda t: t[3])
        objs = sorted({obj for _, obj, _, _ in letters})
        if not objs:
            continue
        shown = [(ch, box[0], box[2]) for ch, _, box, _ in letters]
        main = max(objs, key=lambda k: sum(1 for t in letters if t[1] == k))
        text, rtl = line_text(shown, info[main]["size"])
        if not text.strip():
            continue
        l = min(info[k]["bounds"][0] for k in objs)
        b = min(info[k]["bounds"][1] for k in objs)
        r = max(info[k]["bounds"][2] for k in objs)
        t = max(info[k]["bounds"][3] for k in objs)
        o = info[main]
        font = o["font"] or ("Courier New" if o["fixed"] else "Times New Roman" if o["serif"] else "Arial")
        lines.append({"objs": objs, "rect": [round(v, 2) for v in geo.rect_to_display(l, b, r, t)],
                      "text": text, "rtl": rtl, "size": round(o["size"], 2), "font": font,
                      "serif": o["serif"], "bold": o["bold"], "italic": o["italic"], "color": o["color"],
                      "base": round(o["base"], 2)})
    lines.sort(key=lambda d: (round(d["rect"][1]), d["rect"][0]))
    return lines


def render(doc, index: int, scale: float, hide: tuple = ()) -> tuple[bytes, int, int, int]:
    """A picture of the page (RGBX), without the pieces that were changed, moved or deleted.
    Call with PDFIUM held. (The page itself isn't changed: a page loaded again is as it was.)"""
    import pypdfium2.raw as c
    page = doc[index]
    taken = []
    try:
        for k in sorted(set(hide), reverse=True):
            if 0 <= k < c.FPDFPage_CountObjects(page.raw):
                o = c.FPDFPage_GetObject(page.raw, k)
                if o and c.FPDFPage_RemoveObject(page.raw, o):
                    taken.append(o)
        bitmap = page.render(scale=scale, may_draw_forms=False, rev_byteorder=True, prefer_bgrx=True)
        try:
            data = bytes(bitmap.buffer)
            return data, bitmap.width, bitmap.height, bitmap.stride
        finally:
            bitmap.close()
    finally:
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
                if flags & 1:  # read only
                    continue
                name = _full_name(w)
                l, b, r, t = (float(v) for v in w["/Rect"])
                rect = geo.rect_to_display(min(l, r), min(b, t), max(l, r), max(b, t))
                value = _inherited(w, "/V")
                field = {"page": pi, "name": name, "rect": [round(v, 2) for v in rect]}
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
                    opts = []
                    for o in _inherited(w, "/Opt") or []:
                        o = o.get_object() if hasattr(o, "get_object") else o
                        opts.append(str(o[1] if isinstance(o, list) and len(o) > 1 else o))
                    if isinstance(value, list):
                        value = value[0] if value else ""
                    field.update(kind="choice", options=opts, value=str(value) if value is not None else "")
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


def text_document(d: dict, doc=None):
    """The QTextDocument a text box is drawn with (the editor's text boxes use the same settings)."""
    from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument, QTextOption
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
    # (as wide as its longest line: right-to-left lines then line up on the right)
    doc.setTextWidth(float(d["width"]) if d.get("width") else -1)
    if not d.get("width"):
        doc.setTextWidth(doc.idealWidth())
    return doc


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
        if kind == "text":
            doc = text_document(d)
            p.translate(float(d["x"]), float(d["y"]))
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
    writer.setCreator("Corefold")

    def size_page(i):
        w, h = sizes[i]
        writer.setPageLayout(QPageLayout(QPageSize(QSizeF(w, h), QPageSize.Point), QPageLayout.Portrait,
                                         QMarginsF(0, 0, 0, 0)))
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
    return bytes(data)


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


def _take_out(src: Path, password: str, changes: dict[int, dict]) -> bytes:
    """With PDFium: the old pieces that were changed or deleted are taken out of their pages, and the
    moved ones are moved. Gives the PDF as bytes (without its password: the new copy gets it again)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    with PDFIUM:
        doc = pdfium.PdfDocument(str(src), password=password or None)
        try:
            for index, change in changes.items():
                page = doc[index]
                try:
                    geo = page_geo(page)
                    count = c.FPDFPage_CountObjects(page.raw)
                    gone = {k for k in change.get("remove", ()) if 0 <= k < count}
                    # what a cover hides completely is taken out too (covered words are really gone)
                    for x, y, w, h in change.get("covers", ()):
                        l, b, r, t = geo.rect_to_user(x, y, w, h)
                        for k in range(count):
                            o = c.FPDFPage_GetObject(page.raw, k)
                            if c.FPDFPageObj_GetType(o) not in (c.FPDF_PAGEOBJ_TEXT, c.FPDF_PAGEOBJ_IMAGE):
                                continue
                            ol, ob, orr, ot = _bounds(o)
                            if ol >= l - 0.5 and ob >= b - 0.5 and orr <= r + 0.5 and ot <= t + 0.5:
                                gone.add(k)
                    for k, (old, new) in change.get("move", {}).items():
                        if k in gone or not 0 <= k < count:
                            continue
                        ol, ob, orr, ot = geo.rect_to_user(*old)
                        nl, nb, nr, nt = geo.rect_to_user(*new)
                        sx = (nr - nl) / (orr - ol) if orr - ol > 0.01 else 1.0
                        sy = (nt - nb) / (ot - ob) if ot - ob > 0.01 else 1.0
                        c.FPDFPageObj_Transform(c.FPDFPage_GetObject(page.raw, k), sx, 0, 0, sy,
                                                nl - sx * ol, nb - sy * ob)
                    for k in sorted(gone, reverse=True):
                        o = c.FPDFPage_GetObject(page.raw, k)
                        if o and c.FPDFPage_RemoveObject(page.raw, o):
                            c.FPDFPageObj_Destroy(o)
                    if not c.FPDFPage_GenerateContent(page.raw):
                        raise RuntimeError(tr("page {number} couldn't be changed", number=index + 1))
                finally:
                    page.close()
            out = io.BytesIO()
            doc.save(out, flags=c.FPDF_REMOVE_SECURITY if password else c.FPDF_NO_INCREMENTAL)
            return out.getvalue()
        finally:
            doc.close()


def _field_picture(writer, w: float, h: float, field: dict, value: str):
    """A text box's look with its new value, made by Qt (Arabic is joined, every PDF app shows it)."""
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QRectF, QSizeF, Qt
    from PySide6.QtGui import QColor, QPageLayout, QPageSize, QPainter, QPdfWriter
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    pw = QPdfWriter(buf)
    pw.setResolution(72)
    pw.setPageLayout(QPageLayout(QPageSize(QSizeF(max(w, 1), max(h, 1)), QPageSize.Point), QPageLayout.Portrait,
                                 QMarginsF(0, 0, 0, 0)))
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
    page = PdfReader(io.BytesIO(bytes(data))).pages[0]
    xo = DecodedStreamObject()
    xo.set_data(page.get_contents().get_data() if page.get_contents() is not None else b"")
    xo.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
               NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(w), FloatObject(h)]),
               NameObject("/Resources"): page["/Resources"].clone(writer)})
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
                holder[NameObject("/V")] = TextStringObject(str(value))
                l, b, r, t = (float(v) for v in w["/Rect"])
                pic = _field_picture(writer, abs(r - l), abs(t - b), field, str(value))
                w[NameObject("/AP")] = DictionaryObject({NameObject("/N"): pic})
            elif field["kind"] in ("check", "radio"):
                state = str(value) if value and value != "/Off" else "/Off"
                holder[NameObject("/V")] = NameObject(state)
                mine = _on_state(w)
                w[NameObject("/AS")] = NameObject(mine if state == mine else "/Off")
    form = writer._root_object.get("/AcroForm")
    if form is not None:
        form.get_object()[NameObject("/NeedAppearances")] = __import__("pypdf").generic.BooleanObject(False)


def save_edits(src: Path, ctx: Ctx) -> Result:
    """ctx.params: edits (the editor's list of dicts), fields ({name: value}), password."""
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.annotations import Text
    edits: list[dict] = list(ctx.params.get("edits") or [])
    values: dict = dict(ctx.params.get("fields") or {})
    password = ctx.params.get("password", "") or ""
    if not edits and not values:
        raise RuntimeError(tr("nothing was changed"))
    # 1. old pieces out (or moved)
    changes: dict[int, dict] = {}
    for d in edits:
        page = int(d.get("page", 0))
        if d.get("replaces"):
            changes.setdefault(page, {}).setdefault("remove", set()).update(int(k) for k in d["replaces"])
        if d.get("kind") == "remove":
            changes.setdefault(page, {}).setdefault("remove", set()).update(int(k) for k in d.get("objs", []))
        elif d.get("kind") == "move":
            for k in d.get("objs", []):
                changes.setdefault(page, {}).setdefault("move", {})[int(k)] = (d["orig"], [d["x"], d["y"], d["w"],
                                                                                          d["h"]])
        elif d.get("kind") == "cover":
            changes.setdefault(page, {}).setdefault("covers", []).append((d["x"], d["y"], d["w"], d["h"]))
    ctx.progress(0.05)
    if changes:
        reader = PdfReader(io.BytesIO(_take_out(src, password, changes)))
    else:
        reader = open_reader(src, password)
    fields = read_fields(reader) if values else []
    ctx.check()
    ctx.progress(0.3)
    writer = PdfWriter(clone_from=reader)
    # 2. highlights, then everything drawn on top
    pages = writer.pages
    drawn: dict[int, list[dict]] = {}
    sizes: dict[int, tuple[float, float]] = {}
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
            pages[i].merge_transformed_page(layer.pages[n], Transformation(geos[i].ctm()))
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
    _fill(writer, values, fields)
    if password:
        writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    ctx.progress(0.9)
    out = ctx.out_path(src, f"{ctx.stem(src)} (edited).pdf")
    write(writer, out)
    return Result([out])


EDIT = Tool("pdftool:edit", tr("Edit"), tr("Save the edited PDF"), save_edits, renames=False)
