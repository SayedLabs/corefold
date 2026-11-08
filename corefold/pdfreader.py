"""Rakiza Core PDF's reader: PDFs opened to read, each in a tab. Scroll through the pages or see two side by
side, zoom to the width or the whole page, find words, select and copy text, follow the PDF's links, jump to
a chapter from its bookmarks, read at night on dark pages, or show the pages one at a time on the whole
screen. Each PDF opens again at the page it was left on.

The pages are drawn by PDFium on a thread of their own (one per open PDF), only the ones in sight and at the
size they're shown, so a long PDF opens at once. The words of a page (where each letter is) are read on the
same thread the first time they're needed: to select, to find or to follow a link. Nothing here is loaded
until a PDF is opened to read."""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QGraphicsItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QStackedWidget, QTabBar, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import icons, pdftools, sheet
from .atomic import tokens
from .atomic.widgets import IconButton
from .core import short_name
from .i18n import tr
from .theme import round_menu

if TYPE_CHECKING:
    from .pdfpage import PdfPage

log = logging.getLogger("corefold")

GAP = 14                 # between pages, in points
MAX_PIXELS = 20_000_000  # the biggest picture of one page kept (a page zoomed far in is drawn a little soft)
KEEP_NEAR = 2            # pages kept sharp around the ones in sight
MAX_CHARS = 60_000       # letters read on one page (a page with more is very rare: the rest can't be selected)
MAX_LINKS = 400
PLACES = 200             # PDFs whose page is remembered
THUMB_W = 96             # the small pictures at the side
ZOOMS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 6.0)
SAFE_SCHEMES = ("http://", "https://", "mailto:")
FITS = ("auto", "width", "page", "")
MAX_COMMENTS = 2000      # comments listed at the side (a PDF with more shows the first)
COMMENT_KINDS = {"/Text": "Note", "/FreeText": "Text", "/Highlight": "Highlight", "/Underline": "Underline",
                 "/StrikeOut": "Strike out", "/Squiggly": "Underline", "/Ink": "Drawing", "/Square": "Box",
                 "/Circle": "Circle", "/Line": "Line", "/Polygon": "Shape", "/PolyLine": "Line",
                 "/Stamp": "Stamp", "/Caret": "Insert", "/FileAttachment": "File", "/Sound": "Sound",
                 "/Redact": "Cover"}
COMMENTS_W = 270         # the side, while it shows the comments (they're words: the pages' width is too narrow)
AUTO_MAX = 1.25          # "auto": the page's width, but a narrow page isn't blown up past this
# dark pages: the page's colours turned round (white paper becomes dark grey, black words light grey) with
# their hue kept, so a red heading stays red and a blue link blue (an inverse turned back round by 180°)
HUE_180 = ((-0.574, 1.430, 0.144), (0.426, 0.430, 0.144), (0.426, 1.430, -0.856))
DARK_LOW, DARK_HIGH = 30, 225


# ----------------------------------------------------------------------------- off the window's thread

def render_page(doc, index: int, scale: float, dark: bool = False) -> tuple[bytes, int, int, int]:
    """A picture of the page (RGBX), with what's typed in its form. Call with PDFIUM held."""
    page = doc[index]
    try:
        bitmap = page.render(scale=scale, may_draw_forms=True, rev_byteorder=True, prefer_bgrx=True)
        try:
            data, w, h, stride = bytes(bitmap.buffer), bitmap.width, bitmap.height, bitmap.stride
        finally:
            bitmap.close()
    finally:
        page.close()
    if dark:
        data, stride = darken(data, w, h, stride), w * 4
    return data, w, h, stride


def darken(data: bytes, w: int, h: int, stride: int) -> bytes:
    """The picture for reading at night (see HUE_180), a band of rows at a time (a big page in one go took
    hundreds of MB for a moment)."""
    import numpy as np
    src = np.frombuffer(data, np.uint8).reshape(h, stride)[:, :w * 4].reshape(h, w, 4)
    out = np.empty((h, w, 4), np.uint8)
    out[..., 3] = 255
    m = np.array(HUE_180, np.float32).T
    k = (DARK_HIGH - DARK_LOW) / 255.0
    for top in range(0, h, 256):
        rgb = 255.0 - src[top:top + 256, :, :3].astype(np.float32)
        rgb = rgb @ m
        np.clip(DARK_LOW + rgb * k, 0, 255, out=rgb)
        out[top:top + 256, :, :3] = rgb.astype(np.uint8)
    return out.tobytes()


def _letter(u: int) -> str:
    if u <= 0 or u >= 0x110000 or 0xD800 <= u <= 0xDFFF:
        return "�"
    return chr(u)


def page_words(doc, index: int) -> dict:
    """The page's text, where each letter is (in points on the page as shown: top left, y down; None for the
    line breaks PDFium adds) and its links ({"rect", "to": ("page", n) or ("uri", address)}). Call with PDFIUM
    held."""
    import pypdfium2.raw as c
    from .pdfedit import page_geo
    page = doc[index]
    try:
        geo = page_geo(page)
        letters: list[str] = []
        boxes: list = []
        links: list[dict] = []
        tp = page.get_textpage()
        try:
            n = min(max(0, c.FPDFText_CountChars(tp.raw)), MAX_CHARS)
            box = c.FS_RECTF()
            for i in range(n):
                letters.append(_letter(c.FPDFText_GetUnicode(tp.raw, i)))
                if c.FPDFText_GetLooseCharBox(tp.raw, i, box) and box.right > box.left and box.top > box.bottom:
                    boxes.append(tuple(round(v, 2) for v in geo.rect_to_display(box.left, box.bottom, box.right,
                                                                                 box.top)))
                else:
                    boxes.append(None)
            try:
                links += _web_links(c, tp.raw, geo)
            except Exception:
                log.debug("the web addresses on page %s couldn't be read", index + 1, exc_info=True)
        finally:
            tp.close()
        try:
            links = _page_links(c, doc, page, geo) + links
        except Exception:
            log.debug("the links on page %s couldn't be read", index + 1, exc_info=True)
    finally:
        page.close()
    return {"text": "".join(letters), "boxes": boxes, "links": links[:MAX_LINKS]}


def _page_links(c, doc, page, geo) -> list[dict]:
    """The PDF's own links: to one of its pages, or to a web address."""
    out: list[dict] = []
    pos = ctypes.c_int(0)
    link = c.FPDF_LINK()
    while len(out) < MAX_LINKS and c.FPDFLink_Enumerate(page.raw, ctypes.byref(pos), ctypes.byref(link)):
        r = c.FS_RECTF()
        if not c.FPDFLink_GetAnnotRect(link, r):
            continue
        to = None
        dest = c.FPDFLink_GetDest(doc.raw, link)
        if not dest:
            action = c.FPDFLink_GetAction(link)
            if action:
                kind = c.FPDFAction_GetType(action)
                if kind == c.PDFACTION_GOTO:
                    dest = c.FPDFAction_GetDest(doc.raw, action)
                elif kind == c.PDFACTION_URI:
                    size = c.FPDFAction_GetURIPath(doc.raw, action, None, 0)
                    if 0 < size < 4096:
                        buf = ctypes.create_string_buffer(size)
                        c.FPDFAction_GetURIPath(doc.raw, action, buf, size)
                        to = ("uri", buf.value.decode("utf-8", "replace"))
        if dest:
            n = c.FPDFDest_GetDestPageIndex(doc.raw, dest)
            if n >= 0:
                to = ("page", int(n))
        if to:
            out.append({"rect": tuple(geo.rect_to_display(r.left, r.bottom, r.right, r.top)), "to": to})
    return out


def _web_links(c, tp, geo) -> list[dict]:
    """Web addresses written in the text (most PDF readers let you click them too)."""
    out: list[dict] = []
    wl = c.FPDFLink_LoadWebLinks(tp)
    if not wl:
        return out
    try:
        for k in range(min(c.FPDFLink_CountWebLinks(wl), 200)):
            n = c.FPDFLink_GetURL(wl, k, None, 0)
            if n <= 1 or n > 4096:
                continue
            buf = (ctypes.c_ushort * n)()
            c.FPDFLink_GetURL(wl, k, buf, n)
            url = bytes(buf).decode("utf-16-le", "replace").split("\x00")[0]
            for j in range(min(c.FPDFLink_CountRects(wl, k), 20)):
                vals = [ctypes.c_double() for _ in range(4)]
                if c.FPDFLink_GetRect(wl, k, j, *(ctypes.byref(v) for v in vals)):
                    left, top, right, bottom = (v.value for v in vals)
                    out.append({"rect": tuple(geo.rect_to_display(left, bottom, right, top)), "to": ("uri", url)})
    finally:
        c.FPDFLink_CloseWebLinks(wl)
    return out


class _Signals(QObject):
    opened = Signal(object)                    # (page sizes, bookmarks, right to left, comments) or an Exception
    image = Signal(int, float, bool, object)   # page, scale, dark, QImage
    big = Signal(int, object)                  # page, QImage (for the whole screen)
    thumb = Signal(int, object)
    words = Signal(int, object)                # page, page_words()


class Renderer:
    """One thread per PDF that draws its pages and reads their words: the newest wishes first."""

    def __init__(self, src: Path, password: str, owner: QObject | None = None):
        self.src, self.password = src, password
        # (owned by the reader and let go of with deleteLater: freed by Python while one of its signals was still
        # being answered, closing a damaged PDF's tab, it took the app down)
        self.sig = _Signals(owner)
        self.cv = threading.Condition()
        self.renders: dict[int, tuple[float, bool]] = {}
        self.bigs: list[tuple[int, float, bool]] = []
        self.thumbs: list[int] = []
        self.words: list[int] = []
        self.thumb_w = THUMB_W
        self.stopped = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="corefold-pdf-reader")
        self.thread.start()

    def want(self, wishes: list[tuple[int, float, bool]]):
        with self.cv:
            self.renders = {i: (s, d) for i, s, d in wishes}
            self.cv.notify()

    def want_big(self, index: int, scale: float, dark: bool):
        with self.cv:
            self.bigs = [(index, scale, dark)]
            self.cv.notify()

    def want_thumbs(self, pages: list[int]):
        with self.cv:
            self.thumbs = list(pages)
            self.cv.notify()

    def want_words(self, pages: list[int], first: bool = False):
        with self.cv:
            for i in pages:
                if i in self.words:
                    if not first:
                        continue
                    self.words.remove(i)
                if first:
                    self.words.insert(0, i)
                else:
                    self.words.append(i)
            self.cv.notify()

    def close(self):
        with self.cv:
            self.stopped = True
            self.renders.clear()
            self.bigs.clear()
            self.thumbs.clear()
            self.words.clear()
            self.cv.notify()
        try:
            self.sig.deleteLater()
        except RuntimeError:
            pass

    def _next(self):
        with self.cv:
            while not self.stopped and not (self.bigs or self.renders or self.words or self.thumbs):
                self.cv.wait()
            if self.stopped:
                return None
            if self.bigs:
                return ("big", *self.bigs.pop(0))
            if self.renders:
                i = next(iter(self.renders))
                return ("render", i, *self.renders.pop(i))
            if self.words:
                return ("words", self.words.pop(0))
            return ("thumb", self.thumbs.pop(0))

    def _run(self):
        doc = None
        try:
            with pdftools.PDFIUM:
                doc = pdftools.open_pdfium(self.src, self.password)
                sizes = [tuple(doc.get_page_size(i)) for i in range(len(doc))]
            try:
                from .pdfmore import read_bookmarks
                marks = read_bookmarks(self.src, self.password)[0]
            except Exception:
                log.debug("the bookmarks of %s couldn't be read", self.src, exc_info=True)
                marks = []
            try:
                comments = read_comments(self.src, self.password)
            except Exception:
                log.debug("the comments of %s couldn't be read", self.src, exc_info=True)
                comments = []
            self.sig.opened.emit((sizes, marks, reads_right_to_left(self.src, self.password), comments))
            while True:
                job = self._next()
                if job is None:
                    return
                try:
                    if job[0] in ("render", "big"):
                        _, i, scale, dark = job
                        with pdftools.PDFIUM:
                            data, w, h, stride = render_page(doc, i, scale, dark)
                        img = QImage(data, w, h, stride, QImage.Format.Format_RGBX8888).copy()
                        if job[0] == "big":
                            self.sig.big.emit(i, img)
                        else:
                            self.sig.image.emit(i, scale, dark, img)
                    elif job[0] == "thumb":
                        i = job[1]
                        pw, _ph = sizes[i]
                        with pdftools.PDFIUM:
                            data, w, h, stride = render_page(doc, i, self.thumb_w / max(1.0, pw))
                        self.sig.thumb.emit(i, QImage(data, w, h, stride, QImage.Format.Format_RGBX8888).copy())
                    else:
                        with pdftools.PDFIUM:
                            found = page_words(doc, job[1])
                        self.sig.words.emit(job[1], found)
                except RuntimeError as e:
                    if "Signal source has been deleted" in str(e) or "already deleted" in str(e):
                        return  # (the reader was closed)
                    log.debug("page %s couldn't be drawn or read", job[1] + 1, exc_info=True)
                except Exception:
                    log.debug("page %s couldn't be drawn or read", job[1] + 1, exc_info=True)
        except Exception as e:
            log.warning("the PDF couldn't be opened to read: %s", e)
            try:
                self.sig.opened.emit(e)
            except RuntimeError:
                pass
        finally:
            if doc is not None:
                with pdftools.PDFIUM:
                    doc.close()


# ----------------------------------------------------------------------------- where things go (no window needed)

def layout(sizes: list[tuple[float, float]], two: bool = False, cover: bool = False,
           rtl: bool = False) -> list[tuple[float, float]]:
    """Where each page's top left corner goes, in points: one page under the other, centred, or two side by
    side (with the first page alone, like a book's cover, if cover; the first of two on the right for a PDF
    that's read right to left, like an Arabic book)."""
    places: list[tuple[float, float]] = []
    y = 0.0
    if not two:
        for w, h in sizes:
            places.append((-w / 2, y))
            y += h + GAP
        return places
    rows: list[list[int]] = []
    i = 0
    if cover and sizes:
        rows.append([0])
        i = 1
    while i < len(sizes):
        rows.append(list(range(i, min(i + 2, len(sizes)))))
        i += 2
    for row in rows:
        h = max(sizes[k][1] for k in row)
        if len(row) == 1:
            w = sizes[row[0]][0]
            places.append((-w / 2 if not (cover and row[0] == 0) else GAP / 2, y))
        else:
            left, right = row
            places.append((-GAP / 2 - sizes[left][0], y))
            places.append((GAP / 2, y))
        y += h + GAP
    if rtl:
        places = [(-x - w, y) for (x, y), (w, _h) in zip(places, sizes)]
    return places


def _pdf_date(value) -> str:
    """A PDF date (D:20261108093000+03'00') as 2026-11-08 09:30 ("" when it isn't one)."""
    t = str(value or "").strip()
    if t.startswith("D:"):
        t = t[2:]
    if len(t) < 8 or not t[:8].isdigit():
        return ""
    out = f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    if len(t) >= 12 and t[8:12].isdigit():
        out += f" {t[8:10]}:{t[10:12]}"
    return out


def _words_of(value, limit: int = 4000) -> str:
    return " ".join(str(value).split())[:limit] if value is not None else ""


def read_comments(src: Path, password: str = "") -> list[dict]:
    """The PDF's comments (notes, highlights, drawings, stamps…) in page order, each a dict of page, kind (the
    words for it, before tr), author, date, text, y (from the top of the page, in points) and replies (the
    same, for the comments written as answers to it)."""
    from .pdftools import open_reader
    reader = open_reader(src, password)
    found: list[dict] = []
    by_ref: dict[tuple[int, int], dict] = {}
    answers: list[tuple[tuple[int, int], dict]] = []
    for n, page in enumerate(reader.pages):
        if len(found) >= MAX_COMMENTS:
            break
        try:
            annots = page.get("/Annots")
            annots = annots.get_object() if annots is not None else None
        except Exception:
            continue
        if not annots:
            continue
        try:
            from .pdfedit import pypdf_geo
            geo = pypdf_geo(page)
        except Exception:
            geo = None
        for ref in annots:
            if len(found) >= MAX_COMMENTS:
                break
            try:
                a = ref.get_object()
                kind = str(a.get("/Subtype", ""))
                if kind not in COMMENT_KINDS:
                    continue  # (links, form boxes, popups: not comments)
                text = _words_of(a.get("/Contents"))
                if kind == "/Stamp" and not text:
                    text = str(a.get("/Name", "")).lstrip("/")
                y = 0.0
                rect = a.get("/Rect")
                if rect is not None and geo is not None:
                    l, b, r, t = (float(v) for v in rect)
                    y = max(0.0, geo.rect_to_display(min(l, r), min(b, t), max(l, r), max(b, t))[1])
                c = {"page": n, "kind": COMMENT_KINDS[kind], "author": _words_of(a.get("/T"), 120),
                     "date": _pdf_date(a.get("/M") or a.get("/CreationDate")), "text": text, "y": round(y, 1),
                     "replies": []}
            except Exception:
                log.debug("a comment on page %s couldn't be read", n + 1, exc_info=True)
                continue
            if getattr(ref, "idnum", None) is not None:
                by_ref[(ref.idnum, ref.generation)] = c
            irt = a.get("/IRT")
            if irt is not None and getattr(irt, "idnum", None) is not None:
                answers.append(((irt.idnum, irt.generation), c))
                continue
            found.append(c)
    for key, c in answers:
        parent = by_ref.get(key)
        if parent is not None:
            parent["replies"].append(c)
        else:
            found.append(c)  # (an answer to a comment that's gone: shown on its own)
    found.sort(key=lambda c: (c["page"], c["y"]))
    return found


def comments_text(name: str, comments: list[dict]) -> str:
    """The comments as plain words, to save as a .txt file."""
    lines = [tr("Comments on {name}", name=name), ""]

    def one(c: dict, indent: str):
        head = " · ".join(x for x in (tr(c["kind"]), c["author"], c["date"]) if x)
        lines.append(f"{indent}{head}")
        if c["text"]:
            lines.append(f"{indent}    {c['text']}")
        for r in c["replies"]:
            one(r, indent + "    ")

    page = -1
    for c in comments:
        if c["page"] != page:
            page = c["page"]
            if len(lines) > 2:
                lines.append("")
            lines.append(tr("Page {number}", number=page + 1))
        one(c, "  ")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- private things, found to be covered

PRIVATE_KINDS = {"phone": "Phone numbers", "email": "Email addresses", "id": "ID and card numbers",
                 "iban": "Bank account numbers (IBAN)"}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")  # (Arabic and Persian digits)


def _private_patterns() -> dict:
    import re
    return {"email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
            "iban": re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}(?![A-Za-z0-9])"),
            "id": re.compile(r"(?<![\d])\d(?:[ -]?\d){9,18}(?![\d])"),
            "phone": re.compile(r"(?<![\w+.])\+?\(?\d[\d ()-]{5,}\d(?![\w]|\.\d)")}


def private_spans(text: str, kinds, words=()) -> list[tuple[int, int]]:
    """Where the private things of these kinds (PRIVATE_KINDS' keys) and these words of one's own are in the
    text, as (start, end), joined where they touch. Arabic digits count as digits."""
    import re
    plain = text.translate(_DIGITS)  # (the same length: the places stay right)
    spans: list[tuple[int, int]] = []
    patterns = _private_patterns()
    for kind in kinds:
        pat = patterns.get(kind)
        if pat is None:
            continue
        for m in pat.finditer(plain):
            found = m.group()
            if kind == "phone":
                digits = sum(ch.isdigit() for ch in found)
                if not 7 <= digits <= 15:  # (a short number is a price or a year, not a phone)
                    continue
            spans.append((m.start(), m.end()))
    for word in words:
        word = " ".join(str(word).split())
        if len(word) < 2:
            continue
        spans += [(m.start(), m.end()) for m in re.finditer(re.escape(word.translate(_DIGITS)), plain,
                                                             re.IGNORECASE)]
    spans.sort()
    joined: list[tuple[int, int]] = []
    for a, b in spans:
        if joined and a <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(b, joined[-1][1]))
        else:
            joined.append((a, b))
    return joined


def find_private(src: Path, password: str, kinds, words=(), progress=None) -> list[tuple[int, list]]:
    """Every page's private things (see private_spans) as boxes to cover: [(page, [(x, y, w, h), ...]), ...],
    in points on the page as shown. A page with no text of its own (a scan) has nothing found."""
    found = []
    with pdftools.PDFIUM:
        doc = pdftools.open_pdfium(src, password)
    try:
        count = len(doc)
        for i in range(count):
            with pdftools.PDFIUM:  # (one page at a time: the reader's pages are drawn in between)
                words_of = page_words(doc, i)
            boxes = []
            for a, b in private_spans(words_of.get("text", ""), kinds, words):
                boxes += [(r.x() - 1, r.y() - 1, r.width() + 2, r.height() + 2) for r in line_rects(words_of, a, b)]
            if boxes:
                found.append((i, boxes))
            if progress is not None:
                progress((i + 1) / max(1, count))
    finally:
        with pdftools.PDFIUM:
            doc.close()
    return found


def reads_right_to_left(src: Path, password: str = "") -> bool:
    """The PDF says it's read right to left (/ViewerPreferences /Direction /R2L): its pages face like an
    Arabic book's."""
    try:
        from .pdftools import open_reader
        prefs = open_reader(src, password).trailer["/Root"].get("/ViewerPreferences")
        prefs = prefs.get_object() if prefs is not None else None
        return bool(prefs) and str(prefs.get("/Direction", "")) == "/R2L"
    except Exception:
        return False


def char_at(words: dict, x: float, y: float, near: bool = False) -> int:
    """The letter at this point of the page (-1 if none). near: the closest one on the same line, or the closest
    one at all, when the point isn't on a letter (to start or end a selection between words)."""
    boxes = words.get("boxes") or []
    best, best_d = -1, 1e18
    for i, b in enumerate(boxes):
        if b is None:
            continue
        bx, by, bw, bh = b
        if bx - 0.5 <= x <= bx + bw + 0.5 and by - 0.5 <= y <= by + bh + 0.5:
            return i
        if near:
            dx = max(bx - x, 0.0, x - (bx + bw))
            dy = max(by - y, 0.0, y - (by + bh))
            d = dy * dy * 9 + dx * dx
            if d < best_d:
                best, best_d = i, d
    if near and best >= 0:
        bx, by, bw, bh = boxes[best]
        if x > bx + bw / 2 and best + 1 < len(boxes):  # (after the middle of a letter: the selection takes it)
            return best + 1
    return best


def word_around(words: dict, i: int) -> tuple[int, int]:
    """The whole word the letter i is in (start, end)."""
    text = words.get("text") or ""
    if not 0 <= i < len(text) or not (text[i].isalnum() or text[i] in "_'’-"):
        return (i, i + 1) if 0 <= i < len(text) else (0, 0)
    a, b = i, i + 1
    while a > 0 and (text[a - 1].isalnum() or text[a - 1] in "_'’-"):
        a -= 1
    while b < len(text) and (text[b].isalnum() or text[b] in "_'’-"):
        b += 1
    return a, b


def line_rects(words: dict, a: int, b: int) -> list[QRectF]:
    """The places of the letters a..b-1, joined into one box per line (to draw a selection or what's found)."""
    out: list[QRectF] = []
    boxes = words.get("boxes") or []
    for i in range(max(0, a), min(b, len(boxes))):
        box = boxes[i]
        if box is None:
            continue
        r = QRectF(*box)
        if out:
            last = out[-1]
            same_line = min(last.bottom(), r.bottom()) - max(last.top(), r.top()) > 0.5 * min(last.height(),
                                                                                                r.height())
            close = r.left() - last.right() < 2 * max(last.height(), r.height()) and \
                last.left() - r.right() < 2 * max(last.height(), r.height())
            if same_line and close:
                out[-1] = last.united(r)
                continue
        out.append(r)
    return out


def text_between(words: dict, a: int, b: int) -> str:
    from .pdfedit import arabic_letters
    text = (words.get("text") or "")[max(0, a):b]
    return arabic_letters(text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufffe", "").replace("\x02", ""))


def find_in(text: str, query: str, match_case: bool) -> list[int]:
    """Where the words are found in the page's text (PDFium's line breaks count as spaces)."""
    if not query:
        return []
    hay = text.replace("\r\n", " \n").replace("\r", " ").replace("\n", " ")
    if len(hay) != len(text):
        hay = text
    if not match_case:
        hay2, needle = hay.casefold(), query.casefold()
        if len(hay2) != len(hay):  # (a letter that changes length when its case is folded)
            hay2, needle = hay.lower(), query.lower()
        hay = hay2
    else:
        needle = query
    out, at = [], hay.find(needle)
    while at >= 0 and len(out) < 5000:
        out.append(at)
        at = hay.find(needle, at + max(1, len(needle)))
    return out


def safe_address(url: str) -> bool:
    return url.strip().lower().startswith(SAFE_SCHEMES) and not any(ch in url for ch in "\r\n\t") and len(url) < 2048


# ----------------------------------------------------------------------------- one open PDF

class Doc:
    """A PDF open in a tab: its pages, what's been read from them and where it was being read."""

    def __init__(self, src: Path, password: str):
        self.src, self.password = src, password
        self.renderer: Renderer | None = None
        self.scene: QGraphicsScene | None = None
        self.pages: list[PageItem] = []
        self.sizes: list[tuple[float, float]] = []
        self.marks: list[list] = []
        self.comments: list[dict] = []
        self.words: dict[int, dict] = {}
        self.thumbs: dict[int, QPixmap] = {}
        self.zoom = 1.0
        self.fit = "auto"                   # "auto" (the width, but not bigger than 125%), "width", "page" or ""
        self.two = False
        self.cover = False
        self.rtl = False                    # (read right to left: two pages face like an Arabic book's)
        self.start_page = 0                 # the page it opens at (where it was left)
        self.ready = False
        self.sel: tuple | None = None       # ((page, letter), (page, letter)), as the mouse went
        self.matches: list[tuple[int, int]] = []   # (page, letter) of every match
        self.match_at = -1

    def close(self):
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None


class PageItem(QGraphicsItem):
    """One page: its picture (sharp when in sight, a small one when not), with the selection and what Find
    found drawn on it."""

    def __init__(self, index: int, w: float, h: float):
        super().__init__()
        self.index, self.w, self.h = index, w, h
        self.pm: QPixmap | None = None
        self.pm_key: tuple | None = None
        self.small: QPixmap | None = None
        self.dark = False
        self.sel: list[QRectF] = []
        self.found: list[QRectF] = []
        self.current: list[QRectF] = []

    def boundingRect(self) -> QRectF:
        return QRectF(-3, -3, self.w + 6, self.h + 6)

    def paint(self, p: QPainter, option, widget=None):
        rect = QRectF(0, 0, self.w, self.h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawRect(rect.translated(0, 1.5))
        p.fillRect(rect, QColor(DARK_LOW, DARK_LOW, DARK_LOW) if self.dark else QColor("#ffffff"))
        pm = self.pm or self.small
        if pm is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(rect, pm, QRectF(pm.rect()))
        if self.found or self.current:
            p.setBrush(QColor(255, 200, 0, 90))
            for r in self.found:
                p.drawRect(r)
            p.setBrush(QColor(255, 120, 0, 140))
            for r in self.current:
                p.drawRect(r)
        if self.sel:
            p.setBrush(QColor(60, 130, 255, 80))
            for r in self.sel:
                p.drawRect(r)

    def keep_small(self):
        if self.pm is not None:
            self.small = self.pm.scaled(max(1, self.pm.width() // 4), max(1, self.pm.height() // 4),
                                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self.pm, self.pm_key = None, None


class ReaderView(QGraphicsView):
    """The pages: drag over words to select them, click a link to follow it, Ctrl + wheel to zoom."""

    def __init__(self, reader: "PdfReader"):
        super().__init__()
        self.reader = reader
        self.setObjectName("pdfReaderView")
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName(tr("The pages"))
        self.pressed: tuple | None = None   # (page, letter) where a selection started
        self.press_pos = None
        self.moved = False

    def drawBackground(self, p, rect):
        t = tokens.current()
        p.fillRect(rect, QColor(18, 18, 20) if self.reader.dark else (t.canvas.darker(108) if not t.dark
                                                                        else t.canvas.darker(130)))

    def _at(self, pos) -> tuple["PageItem | None", QPointF]:
        sp = self.mapToScene(pos)
        for item in self.items(pos):
            if isinstance(item, PageItem):
                return item, item.mapFromScene(sp)
        return None, sp

    def _link(self, page: PageItem | None, pt: QPointF):
        doc = self.reader.doc
        if page is None or doc is None:
            return None
        words = doc.words.get(page.index)
        if not words:
            return None
        for link in words["links"]:
            if QRectF(*link["rect"]).contains(pt):
                return link
        return None

    def mousePressEvent(self, e):
        doc = self.reader.doc
        if e.button() != Qt.LeftButton or doc is None:
            super().mousePressEvent(e)
            return
        self.setFocus()
        page, pt = self._at(e.position().toPoint())
        self.press_pos = e.position().toPoint()
        self.moved = False
        self.pressed = None
        if page is None:
            self.reader.clear_selection()
            return
        words = doc.words.get(page.index)
        if words is None:
            self.reader.ask_words([page.index], first=True)
            self.reader.clear_selection()
            return
        if e.modifiers() & Qt.ShiftModifier and doc.sel:
            i = char_at(words, pt.x(), pt.y(), near=True)
            if i >= 0:
                self.pressed = doc.sel[0]
                self.reader.set_selection(doc.sel[0], (page.index, i))
            return
        i = char_at(words, pt.x(), pt.y(), near=True)
        self.pressed = (page.index, i) if i >= 0 else None
        self.reader.clear_selection()

    def mouseMoveEvent(self, e):
        doc = self.reader.doc
        pos = e.position().toPoint()
        page, pt = self._at(pos)
        if self.pressed is not None and e.buttons() & Qt.LeftButton and doc is not None:
            if self.press_pos is not None and (pos - self.press_pos).manhattanLength() > 3:
                self.moved = True
            if page is not None and page.index in doc.words:
                i = char_at(doc.words[page.index], pt.x(), pt.y(), near=True)
                if i >= 0:
                    self.reader.set_selection(self.pressed, (page.index, i))
            elif page is not None:
                self.reader.ask_words([page.index], first=True)
            self._scroll_near_edge(pos)
            return
        if page is not None and doc is not None and page.index not in doc.words:
            self.reader.ask_words([page.index], first=True)
        if self._link(page, pt) is not None:
            self.viewport().setCursor(Qt.PointingHandCursor)
        elif page is not None and doc is not None and page.index in doc.words and \
                char_at(doc.words[page.index], pt.x(), pt.y()) >= 0:
            self.viewport().setCursor(Qt.IBeamCursor)
        else:
            self.viewport().setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def _scroll_near_edge(self, pos):
        h = self.viewport().height()
        bar = self.verticalScrollBar()
        if pos.y() < 20:
            bar.setValue(bar.value() - 20)
        elif pos.y() > h - 20:
            bar.setValue(bar.value() + 20)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and not self.moved:
            page, pt = self._at(e.position().toPoint())
            link = self._link(page, pt)
            if link is not None:
                self.pressed = None
                self.reader.follow(link)
                return
        self.pressed = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        doc = self.reader.doc
        page, pt = self._at(e.position().toPoint())
        if e.button() == Qt.LeftButton and doc is not None and page is not None and page.index in doc.words:
            i = char_at(doc.words[page.index], pt.x(), pt.y())
            if i >= 0:
                a, b = word_around(doc.words[page.index], i)
                self.reader.set_selection((page.index, a), (page.index, b - 1))
                return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        self.reader.context_menu(e.globalPos())

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.reader.zoom_step(1 if e.angleDelta().y() > 0 else -1, anchor=e.position().toPoint())
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        bar = self.verticalScrollBar()
        k = e.key()
        if k in (Qt.Key_PageDown, Qt.Key_Space) and not e.modifiers() & Qt.ShiftModifier:
            bar.setValue(bar.value() + int(self.viewport().height() * 0.9))
        elif k == Qt.Key_PageUp or (k == Qt.Key_Space and e.modifiers() & Qt.ShiftModifier):
            bar.setValue(bar.value() - int(self.viewport().height() * 0.9))
        elif k == Qt.Key_Home and e.modifiers() & Qt.ControlModifier or k == Qt.Key_Home:
            self.reader.go_to(0)
        elif k == Qt.Key_End:
            doc = self.reader.doc
            if doc is not None and doc.pages:
                self.reader.go_to(len(doc.pages) - 1)
        elif k in (Qt.Key_Right, Qt.Key_Left) and not self.horizontalScrollBar().maximum():
            now = self.reader.current_page()
            forward = (k == Qt.Key_Right) != (self.layoutDirection() == Qt.RightToLeft)
            step = 2 if self.reader.doc is not None and self.reader.doc.two else 1
            self.reader.go_to(now + (step if forward else -step))
        else:
            super().keyPressEvent(e)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.reader.view_moved()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.reader.view_resized()


# ----------------------------------------------------------------------------- the whole screen

class FullScreen(QWidget):
    """The pages one at a time on the whole screen (for showing them to people, or reading with nothing else
    in sight): the arrow keys, a click or the wheel turn the page; Esc ends it."""

    def __init__(self, reader: "PdfReader", host: QWidget):
        super().__init__(host)
        self.reader = reader
        self.host = host
        self.page = 0
        self.pm: QPixmap | None = None
        self.pm_page = -1
        self.setObjectName("pdfFullScreen")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(tr("Full screen"))
        self.was_maximized = self.was_full = False
        self.hint = 0
        self.hint_timer = QTimer(self, singleShot=True, interval=1800)
        self.hint_timer.timeout.connect(self._hide_hint)
        self.hide()

    def start(self, page: int):
        """(made once and kept, hidden, while the reader is there: one deleted after use was deleted twice when its
        window went away)"""
        host = self.host
        self.page = page
        self.pm, self.pm_page = None, -1
        self.setCursor(Qt.BlankCursor)
        self.was_maximized = host.isMaximized()
        self.was_full = host.isFullScreen()
        host.installEventFilter(self)
        if not self.was_full:
            host.showFullScreen()
        self.setGeometry(host.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.ask()
        self._show_hint()

    def eventFilter(self, obj, e):
        if obj is self.host and e.type() == QEvent.Resize:
            self.setGeometry(self.host.rect())
            self.ask()
        return False

    def ask(self):
        doc = self.reader.doc
        if doc is None or not doc.sizes or doc.renderer is None:
            return
        w, h = doc.sizes[self.page]
        dpr = self.devicePixelRatioF()
        scale = min(self.width() / max(1.0, w), self.height() / max(1.0, h)) * dpr
        doc.renderer.want_big(self.page, max(0.05, scale), self.reader.dark)

    def got(self, index: int, img: QImage):
        if index != self.page:
            return
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        self.pm, self.pm_page = pm, index
        self.update()

    def turn(self, step: int):
        doc = self.reader.doc
        if doc is None or not doc.sizes:
            return
        n = max(0, min(len(doc.sizes) - 1, self.page + step))
        if n != self.page:
            self.page = n
            self.ask()
            self._show_hint()

    def _show_hint(self):
        self.hint = 1
        self.hint_timer.start()
        self.update()

    def _hide_hint(self):
        self.hint = 0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if self.pm is not None:
            size = self.pm.deviceIndependentSize()
            k = min(self.width() / max(1.0, size.width()), self.height() / max(1.0, size.height()))
            w, h = size.width() * k, size.height() * k
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.pm,
                         QRectF(self.pm.rect()))
        doc = self.reader.doc
        if self.hint and doc is not None:
            text = tr("{number} of {count}", number=self.page + 1, count=len(doc.sizes))
            f = p.font()
            f.setPointSizeF(11)
            p.setFont(f)
            box = QRectF(0, 0, p.fontMetrics().horizontalAdvance(text) + 28, 32)
            box.moveCenter(QPointF(self.width() / 2, self.height() - 40))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(box, 16, 16)
            p.setPen(QColor(235, 235, 235))
            p.drawText(box, Qt.AlignCenter, text)
        p.end()

    def keyPressEvent(self, e):
        k = e.key()
        back = self.layoutDirection() == Qt.RightToLeft
        if k in (Qt.Key_Escape, Qt.Key_F11):
            self.end()
        elif k in (Qt.Key_Down, Qt.Key_PageDown, Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_N):
            self.turn(1)
        elif k in (Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Backspace, Qt.Key_P):
            self.turn(-1)
        elif k == Qt.Key_Right:
            self.turn(-1 if back else 1)
        elif k == Qt.Key_Left:
            self.turn(1 if back else -1)
        elif k == Qt.Key_Home:
            self.turn(-10 ** 6)
        elif k == Qt.Key_End:
            self.turn(10 ** 6)
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.turn(1)
        elif e.button() == Qt.RightButton:
            self.turn(-1)

    def mouseDoubleClickEvent(self, e):
        self.end()

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def wheelEvent(self, e):
        self.turn(-1 if e.angleDelta().y() > 0 else 1)

    def end(self):
        page = self.page
        self.host.removeEventFilter(self)
        if not self.was_full:
            if self.was_maximized:
                self.host.showMaximized()
            else:
                self.host.showNormal()
        self.reader.full = None
        self.hint_timer.stop()
        self.pm = None
        self.hide()
        self.reader.go_to(page)
        self.reader.view.setFocus()


# ----------------------------------------------------------------------------- the reader

class PdfReader(QWidget):
    """The reader, shown on Rakiza Core PDF's page instead of the cards."""

    closed = Signal()

    def __init__(self, pdf_page: "PdfPage"):
        super().__init__()
        self.pdf = pdf_page
        self.c = pdf_page.c
        self.docs: list[Doc] = []
        self.doc: Doc | None = None
        self.dark = bool(getattr(self.c.settings, "pdf_dark_pages", False))
        self.full: FullScreen | None = None          # (while the pages are on the whole screen)
        self.full_widget: FullScreen | None = None
        self.matches_timer = QTimer(self, singleShot=True, interval=200)
        self.matches_timer.timeout.connect(self._find_now)
        self.visible_timer = QTimer(self, singleShot=True, interval=30)
        self.visible_timer.timeout.connect(self._ask_pages)
        self.thumbs_timer = QTimer(self, singleShot=True, interval=60)
        self.thumbs_timer.timeout.connect(self._ask_thumbs)
        self.copy_waiting = False
        self._build()

    # ---- building

    def _icon_button(self, tip: str, icon: str, fn, checkable: bool = False) -> IconButton:
        muted = self.c.theme.muted
        b = IconButton(tip)
        b.setIcon(icons.icon(icon, muted, tokens.alpha(QColor(muted), 90)))
        b.setCheckable(checkable)
        b.clicked.connect(lambda _=False: fn())
        return b

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        # the tabs (one for each PDF open), with + to open more
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(4)
        self.tabs = QTabBar()
        self.tabs.setObjectName("pdfReaderTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(False)  # (a close button of our own on each tab, in the look of the rest)
        self.tabs.setMovable(True)
        self.tabs.setExpanding(False)
        self.tabs.setElideMode(Qt.ElideMiddle)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabMoved.connect(self._tab_moved)
        tabs_row.addWidget(self.tabs, 1)
        self.open_btn = self._icon_button(tr("Open another PDF (Ctrl + O)"), "add", self.pick_more)
        tabs_row.addWidget(self.open_btn)
        v.addLayout(tabs_row)

        # the tools
        bar = QFrame()
        bar.setObjectName("pdfToolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(4)
        self.side_btn = self._icon_button(tr("Pages and bookmarks at the side"), "layout", self.toggle_side, True)
        row.addWidget(self.side_btn)
        self.find_btn = self._icon_button(tr("Find (Ctrl + F)"), "search", lambda: self.show_find())
        row.addWidget(self.find_btn)
        sep = QFrame()
        sep.setObjectName("pdfToolSep")
        sep.setFixedWidth(1)
        row.addWidget(sep)
        self.page_box = QLineEdit()
        self.page_box.setObjectName("pdfPageBox")
        self.page_box.setFixedWidth(52)
        self.page_box.setAlignment(Qt.AlignCenter)
        self.page_box.setAccessibleName(tr("Page"))
        self.page_box.setToolTip(tr("Type a page number and press Enter"))
        self.page_box.returnPressed.connect(self._page_typed)
        row.addWidget(self.page_box)
        self.count_label = QLabel()
        self.count_label.setObjectName("muted")
        row.addWidget(self.count_label)
        sep = QFrame()
        sep.setObjectName("pdfToolSep")
        sep.setFixedWidth(1)
        row.addWidget(sep)
        row.addWidget(self._icon_button(tr("Zoom out (Ctrl + -)"), "zoom_out", lambda: self.zoom_step(-1)))
        self.zoom_btn = QPushButton("100%")
        self.zoom_btn.setObjectName("small")
        self.zoom_btn.setCursor(Qt.PointingHandCursor)
        self.zoom_btn.setToolTip(tr("Zoom"))
        self.zoom_btn.setAccessibleName(tr("Zoom"))
        self.zoom_btn.setMinimumWidth(64)
        self.zoom_btn.clicked.connect(self._zoom_menu)
        row.addWidget(self.zoom_btn)
        row.addWidget(self._icon_button(tr("Zoom in (Ctrl + +)"), "zoom_in", lambda: self.zoom_step(1)))
        sep = QFrame()
        sep.setObjectName("pdfToolSep")
        sep.setFixedWidth(1)
        row.addWidget(sep)
        self.two_btn = self._icon_button(tr("Two pages side by side"), "document_multiple", self.toggle_two, True)
        row.addWidget(self.two_btn)
        self.dark_btn = self._icon_button(tr("Dark pages, for reading at night"), "weather_moon", self.toggle_dark,
                                          True)
        self.dark_btn.setChecked(self.dark)
        row.addWidget(self.dark_btn)
        self.full_btn = self._icon_button(tr("Full screen (F11)"), "full_screen_maximize", self.full_screen)
        row.addWidget(self.full_btn)
        row.addStretch()
        self.edit_btn = QPushButton(tr("Edit"))
        self.edit_btn.setToolTip(tr("Open this PDF in the editor"))
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.clicked.connect(self.edit_this)
        row.addWidget(self.edit_btn)
        self.more_btn = self._icon_button(tr("More"), "more_horizontal", self._more_menu)
        row.addWidget(self.more_btn)
        self.close_btn = QPushButton(tr("Close"))
        self.close_btn.setToolTip(tr("Close the reader (Esc)"))
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(lambda: self.close_reader())
        row.addWidget(self.close_btn)
        v.addWidget(bar)

        # Find
        self.find_bar = QFrame()
        self.find_bar.setObjectName("pdfFindBar")
        fl = QHBoxLayout(self.find_bar)
        fl.setContentsMargins(6, 2, 6, 2)
        fl.setSpacing(6)
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText(tr("Find in this PDF"))
        self.find_box.setAccessibleName(tr("Find in this PDF"))
        self.find_box.setClearButtonEnabled(True)
        self.find_box.textChanged.connect(lambda _t: self.matches_timer.start())
        self.find_box.returnPressed.connect(lambda: self.next_match(
            -1 if QGuiApplication.keyboardModifiers() & Qt.ShiftModifier else 1))
        fl.addWidget(self.find_box, 1)
        self.case_box = QCheckBox(tr("Match case"))
        self.case_box.toggled.connect(lambda _on: self._find_now())
        fl.addWidget(self.case_box)
        self.found_label = QLabel()
        self.found_label.setObjectName("muted")
        self.found_label.setMinimumWidth(90)
        fl.addWidget(self.found_label)
        fl.addWidget(self._icon_button(tr("Previous (Shift + F3)"), "chevron_left", lambda: self.next_match(-1)))
        fl.addWidget(self._icon_button(tr("Next (F3)"), "chevron_right", lambda: self.next_match(1)))
        fl.addWidget(self._icon_button(tr("Close (Esc)"), "dismiss", self.hide_find))
        self.find_bar.hide()
        v.addWidget(self.find_bar)

        body = QHBoxLayout()
        body.setSpacing(8)
        # the side: small pictures of the pages, and the PDF's bookmarks
        self.side = QFrame()
        self.side.setObjectName("pdfPagesSide")
        self.side.setFixedWidth(THUMB_W + 52)
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(4)
        pick = QHBoxLayout()
        pick.setSpacing(2)
        self.side_pages_btn = QToolButton()
        self.side_marks_btn = QToolButton()
        self.side_notes_btn = QToolButton()
        muted = self.c.theme.muted
        for b, words, icon, k in ((self.side_pages_btn, tr("Pages"), "document_multiple", 0),
                                  (self.side_marks_btn, tr("Chapters"), "text_description", 1),
                                  (self.side_notes_btn, tr("Comments"), "chat", 2)):
            # (pictures, not words: three words don't fit the narrow side in every language)
            b.setIcon(icons.icon(icon, muted, tokens.alpha(QColor(muted), 70)))
            b.setIconSize(QSize(16, 16))
            b.setToolTip(words)
            b.setAccessibleName(words)
            b.setObjectName("pdfSideTab")
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=k: self._side_show(k))
            pick.addWidget(b, 1)
        sl.addLayout(pick)
        self.side_stack = QStackedWidget()
        self.thumb_list = QListWidget()
        self.thumb_list.setObjectName("pdfReaderPages")
        self.thumb_list.setViewMode(QListView.IconMode)
        self.thumb_list.setFlow(QListView.TopToBottom)
        self.thumb_list.setWrapping(False)
        self.thumb_list.setMovement(QListView.Static)
        self.thumb_list.setIconSize(QSize(THUMB_W, int(THUMB_W * 1.42)))
        self.thumb_list.setSpacing(4)
        self.thumb_list.setUniformItemSizes(True)
        self.thumb_list.setFrameShape(QFrame.NoFrame)
        self.thumb_list.itemClicked.connect(lambda it: self.go_to(self.thumb_list.row(it)))
        self.thumb_list.verticalScrollBar().valueChanged.connect(lambda _v: self.thumbs_timer.start())
        self.side_stack.addWidget(self.thumb_list)
        self.mark_tree = QTreeWidget()
        self.mark_tree.setObjectName("pdfReaderMarks")
        self.mark_tree.setHeaderHidden(True)
        self.mark_tree.setFrameShape(QFrame.NoFrame)
        self.mark_tree.itemClicked.connect(lambda it, _c: self.go_to(int(it.data(0, Qt.UserRole) or 0)))
        self.side_stack.addWidget(self.mark_tree)
        # the comments (notes, highlights, stamps… people put on the PDF): click one to go to it
        notes = QWidget()
        nl = QVBoxLayout(notes)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(4)
        self.notes_list = QListWidget()
        self.notes_list.setObjectName("pdfReaderComments")
        self.notes_list.setWordWrap(True)
        self.notes_list.setFrameShape(QFrame.NoFrame)
        self.notes_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.notes_list.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.notes_list.itemClicked.connect(self._note_clicked)
        nl.addWidget(self.notes_list, 1)
        self.notes_save = QPushButton(tr("Save as a text file…"))
        self.notes_save.setObjectName("small")
        self.notes_save.setCursor(Qt.PointingHandCursor)
        self.notes_save.clicked.connect(self.save_comments)
        nl.addWidget(self.notes_save)
        self.side_stack.addWidget(notes)
        sl.addWidget(self.side_stack, 1)
        body.addWidget(self.side)
        self.view = ReaderView(self)
        self.blank_scene = QGraphicsScene(self)
        self.view.setScene(self.blank_scene)
        body.addWidget(self.view, 1)
        v.addLayout(body, 1)
        # (only once it's in its place: shown before, it would flash as a window of its own)
        side_on = bool(getattr(self.c.settings, "pdf_read_side", True))
        self.side.setVisible(side_on)
        self.side_btn.setChecked(side_on)
        self.status = QLabel()
        self.status.setObjectName("subtle")
        self.status.hide()
        v.addWidget(self.status)

        for keys, fn in ((QKeySequence.Find, lambda: self.show_find()), ("F3", lambda: self.next_match(1)),
                         ("Shift+F3", lambda: self.next_match(-1)), (QKeySequence.Copy, self.copy),
                         (QKeySequence.SelectAll, self.select_all), ("Ctrl+O", self.pick_more),
                         ("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
                         ("Ctrl+Tab", lambda: self._tab_step(1)), ("Ctrl+Shift+Tab", lambda: self._tab_step(-1)),
                         ("Ctrl+=", lambda: self.zoom_step(1)), ("Ctrl++", lambda: self.zoom_step(1)),
                         ("Ctrl+-", lambda: self.zoom_step(-1)), ("Ctrl+0", lambda: self.set_fit("width")),
                         ("Ctrl+1", lambda: self.set_zoom(1.0)), ("Ctrl+2", lambda: self.set_fit("page")),
                         ("F11", self.full_screen), ("Ctrl+L", self.full_screen), ("Ctrl+P", self.print_this)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._side_show(0)
        self._update_bar()

    # ---- opening and closing PDFs

    def open(self, src: Path, password: str = ""):
        """Opens the PDF in a new tab (or shows its tab, if it's open already)."""
        key = os.path.normcase(str(src))
        for n, d in enumerate(self.docs):
            if os.path.normcase(str(d.src)) == key:
                self.tabs.setCurrentIndex(n)
                return
        doc = Doc(src, password)
        place = (getattr(self.c.settings, "pdf_places", None) or {}).get(key)
        if isinstance(place, list) and len(place) >= 3:
            try:
                doc.start_page = max(0, int(place[0]))
                doc.zoom = max(ZOOMS[0], min(ZOOMS[-1], float(place[1])))
                doc.fit = place[2] if place[2] in FITS else "auto"
                doc.two = bool(place[3]) if len(place) > 3 else False
            except (TypeError, ValueError):
                pass
        doc.scene = QGraphicsScene(self)
        doc.renderer = Renderer(src, password, self)
        sig = doc.renderer.sig
        sig.opened.connect(lambda r, d=doc: self._opened(d, r))
        sig.image.connect(lambda i, s, dk, img, d=doc: self._image(d, i, s, dk, img))
        sig.big.connect(lambda i, img, d=doc: self._big(d, i, img))
        sig.thumb.connect(lambda i, img, d=doc: self._thumb(d, i, img))
        sig.words.connect(lambda i, w, d=doc: self._words(d, i, w))
        self.docs.append(doc)
        n = self.tabs.addTab(short_name(src.name, 32))
        self.tabs.setTabToolTip(n, str(src))
        shut = QToolButton(self.tabs)  # (made with its parent: QTabBar deletes it later itself, when the tab goes)
        shut.setObjectName("pdfTabClose")
        shut.setAutoRaise(True)
        shut.setCursor(Qt.PointingHandCursor)
        shut.setToolTip(tr("Close (Ctrl + W)"))
        shut.setAccessibleName(tr("Close (Ctrl + W)"))
        shut.setIcon(icons.icon("dismiss", self.c.theme.muted))
        shut.setIconSize(QSize(12, 12))
        shut.setFixedSize(20, 20)
        shut.clicked.connect(lambda _=False, d=doc: self.close_tab(self.docs.index(d)) if d in self.docs else None)
        self.tabs.setTabButton(n, QTabBar.RightSide, shut)
        self.tabs.setCurrentIndex(n)
        self._say(tr("Opening…"))

    def _opened(self, doc: Doc, result):
        if doc not in self.docs:
            return
        if isinstance(result, Exception):
            self.c.tell(tr("Rakiza Core PDF"), tr("{name} couldn't be read. It may be damaged.",
                                                  name=short_name(doc.src.name)))
            self.close_tab(self.docs.index(doc))
            return
        doc.sizes, doc.marks, doc.rtl, doc.comments = result
        doc.ready = True
        for i, (w, h) in enumerate(doc.sizes):
            item = PageItem(i, w, h)
            item.dark = self.dark
            doc.pages.append(item)
            doc.scene.addItem(item)
        self._place(doc)
        if doc is self.doc:
            self._show_doc(doc, first=True)

    def close_tab(self, n: int):
        if not 0 <= n < len(self.docs):
            return
        doc = self.docs.pop(n)
        self._remember(doc)
        doc.close()
        if doc is self.doc:
            self.doc = None
        self.tabs.blockSignals(True)
        self.tabs.removeTab(n)
        self.tabs.blockSignals(False)
        if doc.scene is not None:
            if self.view.scene() is doc.scene:
                self.view.setScene(self.blank_scene)
            doc.scene.deleteLater()
        if not self.docs:
            self.close_reader()
            return
        self._tab_changed(self.tabs.currentIndex())

    def close_reader(self, tell: bool = True):
        """Closes every PDF (each remembers where it was) and goes back."""
        if self.full is not None:
            self.full.end()
        for doc in self.docs:
            self._remember(doc)
            doc.close()
            if doc.scene is not None:
                doc.scene.deleteLater()
        self.docs = []
        self.doc = None
        self.view.setScene(self.blank_scene)
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.tabs.blockSignals(False)
        self.thumb_list.clear()
        self.mark_tree.clear()
        self.hide_find()
        self.c.settings.save()
        if tell:
            self.closed.emit()

    def close_renderer(self):
        """Lets go of the PDFs (the window is going away)."""
        self.close_reader(tell=False)

    def go_back(self) -> bool:
        """Esc / the back button: Find closes first, then the reader."""
        if self.find_bar.isVisible():
            self.hide_find()
            return True
        if self.doc is not None and self.doc.sel:
            self.clear_selection()
            return True
        self.close_reader()
        return True

    def _remember(self, doc: Doc):
        if not doc.ready:
            return
        places = dict(getattr(self.c.settings, "pdf_places", None) or {})
        key = os.path.normcase(str(doc.src))
        page = self.current_page(doc) if doc is self.doc else doc.start_page
        places.pop(key, None)
        places[key] = [page, round(doc.zoom, 3), doc.fit, doc.two]
        while len(places) > PLACES:
            places.pop(next(iter(places)))
        self.c.settings.pdf_places = places

    def pick_more(self):
        from .pdfpage import PDF_FILTER
        names, _ = QFileDialog.getOpenFileNames(self, tr("Pick the PDFs"), str(self.doc.src.parent if self.doc
                                                                               else Path.home()), PDF_FILTER)
        files = [Path(n) for n in names if n]
        if files:
            self.pdf.open_reader(files)

    def _tab_changed(self, n: int):
        if self.doc is not None and self.doc.ready and 0 <= n < len(self.docs) and self.docs[n] is not self.doc:
            self.doc.start_page = self.current_page(self.doc)
        self.doc = self.docs[n] if 0 <= n < len(self.docs) else None
        if self.doc is None:
            return
        self._show_doc(self.doc, first=False)

    def _tab_moved(self, frm: int, to: int):
        self.docs.insert(to, self.docs.pop(frm))

    def _tab_step(self, step: int):
        if self.tabs.count() > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % self.tabs.count())

    def _show_doc(self, doc: Doc, first: bool):
        """Puts this PDF's pages in sight (at the page it was left on)."""
        self.view.setScene(doc.scene)
        self._fill_side(doc)
        if not doc.ready:
            self._update_bar()
            return
        self._say("")
        self._apply_zoom(doc, keep=False)
        QTimer.singleShot(0, lambda d=doc: self.go_to(d.start_page, d) if d is self.doc else None)
        if self.find_bar.isVisible() and self.find_box.text():
            self._find_now()
        self._update_bar()

    # ---- placing and drawing the pages

    def _place(self, doc: Doc):
        places = layout(doc.sizes, doc.two, doc.cover, doc.rtl)
        for item, (x, y) in zip(doc.pages, places):
            item.setPos(x, y)
        if doc.pages:
            rect = doc.scene.itemsBoundingRect().adjusted(-GAP + 3, -GAP / 2, GAP - 3, GAP / 2)
            doc.scene.setSceneRect(rect)

    def _row_width(self, doc: Doc) -> float:
        if not doc.sizes:
            return 600.0
        if not doc.two:
            return max(w for w, _ in doc.sizes)
        return max(w for w, _ in doc.sizes) * 2 + GAP

    def _fit_zoom(self, doc: Doc, how: str) -> float:
        vw = max(100, self.view.viewport().width() - 4)
        vh = max(100, self.view.viewport().height() - 4)
        width_zoom = vw / max(1.0, self._row_width(doc) + 2 * GAP)
        if how == "page" and doc.sizes:
            page = self.current_page(doc) if doc is self.doc else doc.start_page
            h = doc.sizes[max(0, min(page, len(doc.sizes) - 1))][1]
            return max(ZOOMS[0], min(width_zoom, vh / max(1.0, h + GAP)))
        if how == "auto":
            width_zoom = min(width_zoom, AUTO_MAX)
        return max(ZOOMS[0], min(ZOOMS[-1], width_zoom))

    def _apply_zoom(self, doc: Doc, keep: bool = True, anchor=None):
        if doc.fit:
            doc.zoom = self._fit_zoom(doc, doc.fit)
        old = None
        if keep and doc is self.doc:
            at = anchor if anchor is not None else self.view.viewport().rect().center()
            old = (self.view.mapToScene(at), at)
        self.view.resetTransform()
        self.view.scale(doc.zoom, doc.zoom)
        if old is not None:
            sp, at = old
            now = self.view.mapToScene(at)
            bar_h, bar_v = self.view.horizontalScrollBar(), self.view.verticalScrollBar()
            bar_h.setValue(bar_h.value() + round((sp.x() - now.x()) * doc.zoom))
            bar_v.setValue(bar_v.value() + round((sp.y() - now.y()) * doc.zoom))
        self._update_bar()
        self.visible_timer.start()

    def set_fit(self, how: str):
        doc = self.doc
        if doc is None or not doc.ready:
            return
        page = self.current_page()
        doc.fit = how
        self._apply_zoom(doc, keep=False)
        self.go_to(page)

    def set_zoom(self, zoom: float, anchor=None):
        doc = self.doc
        if doc is None or not doc.ready:
            return
        doc.fit = ""
        doc.zoom = max(ZOOMS[0], min(ZOOMS[-1], zoom))
        self._apply_zoom(doc, anchor=anchor)

    def zoom_step(self, step: int, anchor=None):
        doc = self.doc
        if doc is None:
            return
        if step > 0:
            z = next((z for z in ZOOMS if z > doc.zoom * 1.01), ZOOMS[-1])
        else:
            z = next((z for z in reversed(ZOOMS) if z < doc.zoom * 0.99), ZOOMS[0])
        self.set_zoom(z, anchor=anchor)

    def view_resized(self):
        doc = self.doc
        if doc is not None and doc.ready and doc.fit:
            page = self.current_page()
            self._apply_zoom(doc, keep=False)
            self.go_to(page)
        self.visible_timer.start()

    def view_moved(self):
        self.visible_timer.start()
        self._update_page_box()

    def visible_pages(self, doc: Doc | None = None) -> list[int]:
        doc = doc or self.doc
        if doc is None or not doc.pages:
            return []
        area = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        return [p.index for p in doc.pages if p.sceneBoundingRect().intersects(area)]

    def current_page(self, doc: Doc | None = None) -> int:
        doc = doc or self.doc
        if doc is None or not doc.pages:
            return 0
        if doc is not self.doc:
            return doc.start_page
        area = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        line = area.top() + area.height() * 0.3
        best, best_d = 0, 1e18
        for p in doc.pages:
            r = p.sceneBoundingRect()
            if r.top() <= line <= r.bottom():
                return p.index
            d = min(abs(r.top() - line), abs(r.bottom() - line))
            if d < best_d:
                best, best_d = p.index, d
        return best

    def go_to(self, page: int, doc: Doc | None = None, y: float | None = None):
        doc = doc or self.doc
        if doc is None or doc is not self.doc or not doc.pages:
            return
        page = max(0, min(len(doc.pages) - 1, page))
        item = doc.pages[page]
        r = item.sceneBoundingRect()
        top = doc.scene.sceneRect().top()
        if y:  # (a place on the page: a little above it, so what's just over it is seen too)
            self.view.verticalScrollBar().setValue(round((r.top() + max(0.0, y - 40) - top) * doc.zoom))
        else:
            self.view.verticalScrollBar().setValue(round((r.top() - GAP / 2 - top) * doc.zoom))
        self.view.centerOn(r.center().x(), self.view.mapToScene(self.view.viewport().rect().center()).y())
        self._update_page_box()
        self.visible_timer.start()

    def _ask_pages(self):
        doc = self.doc
        if doc is None or doc.renderer is None or not doc.pages:
            return
        seen = self.visible_pages(doc)
        if not seen:
            return
        near = set(range(max(0, seen[0] - KEEP_NEAR), min(len(doc.pages), seen[-1] + KEEP_NEAR + 1)))
        dpr = self.view.devicePixelRatioF()
        wishes = []
        for i in list(seen) + sorted(near - set(seen)):
            item = doc.pages[i]
            scale = doc.zoom * dpr
            if item.w * item.h * scale * scale > MAX_PIXELS:
                scale = (MAX_PIXELS / max(1.0, item.w * item.h)) ** 0.5
            key = (round(scale, 3), self.dark)
            if item.pm_key != key:
                wishes.append((i, scale, self.dark))
        for item in doc.pages:
            if item.index not in near:
                item.keep_small()
        doc.renderer.want(wishes)
        self.ask_words(seen)
        self._update_page_box()

    def ask_words(self, pages: list[int], first: bool = False):
        doc = self.doc
        if doc is None or doc.renderer is None:
            return
        doc.renderer.want_words([i for i in pages if i not in doc.words], first=first)

    def _image(self, doc: Doc, i: int, scale: float, dark: bool, img: QImage):
        if doc not in self.docs or not 0 <= i < len(doc.pages):
            return
        item = doc.pages[i]
        pm = QPixmap.fromImage(img)
        item.pm = pm
        item.pm_key = (round(scale, 3), dark)
        item.dark = dark
        item.small = None
        item.update()

    def _big(self, doc: Doc, i: int, img: QImage):
        if self.full is not None and doc is self.doc:
            self.full.got(i, img)

    def _thumb(self, doc: Doc, i: int, img: QImage):
        if doc not in self.docs:
            return
        pm = QPixmap.fromImage(img)
        doc.thumbs[i] = pm
        if doc is self.doc and i < self.thumb_list.count():
            self.thumb_list.item(i).setIcon(pm)

    def _words(self, doc: Doc, i: int, words: dict):
        if doc not in self.docs:
            return
        doc.words[i] = words
        if doc is not self.doc:
            return
        if doc.sel and min(doc.sel)[0] <= i <= max(doc.sel)[0]:
            self._draw_selection()
        if self.find_bar.isVisible() and self.find_box.text():
            self.matches_timer.start()
        if self.copy_waiting and doc.sel and all(p in doc.words for p in range(min(doc.sel)[0],
                                                                                max(doc.sel)[0] + 1)):
            self.copy()

    # ---- the side

    def toggle_side(self):
        on = self.side.isHidden()
        self.side.setVisible(on)
        self.side_btn.setChecked(on)
        self.c.settings.pdf_read_side = on
        if on:
            self.thumbs_timer.start()

    def _side_show(self, k: int):
        self.side_stack.setCurrentIndex(k)
        self.side_pages_btn.setChecked(k == 0)
        self.side_marks_btn.setChecked(k == 1)
        self.side_notes_btn.setChecked(k == 2)
        self.side.setFixedWidth(COMMENTS_W if k == 2 else THUMB_W + 52)
        if k == 0:
            self.thumbs_timer.start()

    def _fill_side(self, doc: Doc):
        self.thumb_list.clear()
        blank = QPixmap(THUMB_W, int(THUMB_W * 1.3))
        blank.fill(QColor("#ffffff"))
        for i in range(len(doc.sizes)):
            it = QListWidgetItem(doc.thumbs.get(i) or blank, str(i + 1))
            it.setTextAlignment(Qt.AlignHCenter)
            it.setSizeHint(QSize(THUMB_W + 16, int(THUMB_W * 1.42) + 26))
            self.thumb_list.addItem(it)
        self.mark_tree.clear()
        parents: list[QTreeWidgetItem] = []
        for level, title, page in doc.marks:
            it = QTreeWidgetItem([str(title)])
            it.setData(0, Qt.UserRole, int(page))
            it.setToolTip(0, tr("Page {number}", number=int(page) + 1))
            parents = parents[:level]
            if parents:
                parents[-1].addChild(it)
            else:
                self.mark_tree.addTopLevelItem(it)
            parents.append(it)
        self.side_marks_btn.setEnabled(bool(doc.marks))
        self._fill_comments(doc)
        if doc.marks and doc.ready and not self.side_marks_btn.isChecked() and doc.start_page == 0:
            self._side_show(1)  # (a PDF with chapters shows them first)
        elif (self.side_stack.currentIndex() == 1 and not doc.marks) or (
                self.side_stack.currentIndex() == 2 and not doc.comments):
            self._side_show(0)
        self.thumbs_timer.start()

    def _fill_comments(self, doc: Doc):
        self.notes_list.clear()
        n = 0
        arrow = "› "  # (an answer, under what it answers; turned the other way in Arabic by itself)

        def add(c: dict, reply: bool):
            nonlocal n
            n += 1
            head = " · ".join(x for x in (tr(c["kind"]), c["author"]) if x)
            if not reply:
                head = f"{tr('Page {number}', number=c['page'] + 1)} · {head}"
            # (each line set its own way: English words keep their full stop at the end in the Arabic look)
            words = f"{head}\n\u2068{c['text']}\u2069" if c["text"] else head
            it = QListWidgetItem((arrow if reply else "") + words)
            it.setData(Qt.UserRole, (int(c["page"]), float(c["y"])))
            if c["date"]:
                it.setToolTip(c["date"])
            self.notes_list.addItem(it)
            for r in c["replies"]:
                add(r, True)

        for c in doc.comments:
            add(c, False)
        self.side_notes_btn.setEnabled(bool(doc.comments))
        self.side_notes_btn.setToolTip(tr("Comments ({count})", count=n) if n else tr("Comments"))
        self.notes_save.setVisible(bool(doc.comments))

    def _note_clicked(self, it: QListWidgetItem):
        page, y = it.data(Qt.UserRole) or (0, 0.0)
        self.go_to(int(page), y=float(y))

    def save_comments(self):
        doc = self.doc
        if doc is None or not doc.comments:
            return
        start = doc.src.with_name(f"{doc.src.stem} - {tr('Comments')}.txt")
        name, _ = QFileDialog.getSaveFileName(self, tr("Save as a text file…").rstrip("…"), str(start),
                                              tr("Text (*.txt)"))
        if not name:
            return
        try:
            Path(name).write_text(comments_text(doc.src.name, doc.comments), encoding="utf-8")
        except OSError as e:
            self.c.tell(tr("Rakiza Core PDF"), str(e))
            return
        self._say(tr("Saved"))

    def _ask_thumbs(self):
        doc = self.doc
        if doc is None or doc.renderer is None or not self.side.isVisible() or self.side_stack.currentIndex():
            return
        lst = self.thumb_list
        if not lst.count():
            return
        top = lst.indexAt(lst.viewport().rect().topLeft() + lst.viewport().rect().center() * 0)
        first = max(0, top.row()) if top.isValid() else 0
        rows = max(4, lst.viewport().height() // max(1, int(THUMB_W * 1.42) + 30) + 3)
        doc.renderer.want_thumbs([i for i in range(first, min(lst.count(), first + rows)) if i not in doc.thumbs])

    # ---- the bar

    def _update_bar(self):
        doc = self.doc
        ready = doc is not None and doc.ready
        for w in (self.page_box, self.zoom_btn, self.two_btn, self.full_btn, self.edit_btn, self.find_btn):
            w.setEnabled(ready)
        if not ready:
            self.count_label.setText("")
            self.page_box.setText("")
            return
        self.zoom_btn.setText(f"{round(doc.zoom * 100)}%")
        self.two_btn.setChecked(doc.two)
        self.count_label.setText(tr("of {count}", count=len(doc.pages)))
        self._update_page_box()

    def _update_page_box(self):
        doc = self.doc
        if doc is None or not doc.ready:
            return
        n = self.current_page()
        if not self.page_box.hasFocus():
            self.page_box.setText(str(n + 1))
        if self.thumb_list.count() > n and self.thumb_list.currentRow() != n:
            self.thumb_list.blockSignals(True)
            self.thumb_list.setCurrentRow(n)
            self.thumb_list.blockSignals(False)

    def _page_typed(self):
        try:
            n = int(self.page_box.text().strip()) - 1
        except ValueError:
            self._update_page_box()
            return
        self.go_to(n)
        self.view.setFocus()

    def _zoom_menu(self):
        m = QMenu(self)
        round_menu(m)
        doc = self.doc
        for words, how in ((tr("Automatic"), "auto"), (tr("Fit the width (Ctrl + 0)"), "width"),
                           (tr("Whole page (Ctrl + 2)"), "page")):
            a = m.addAction(words)
            a.setCheckable(True)
            a.setChecked(doc is not None and doc.fit == how)
            a.triggered.connect(lambda _=False, h=how: self.set_fit(h))
        m.addSeparator()
        for z in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0):
            a = m.addAction(f"{round(z * 100)}%")
            a.setCheckable(True)
            a.setChecked(doc is not None and not doc.fit and abs(doc.zoom - z) < 0.005)
            a.triggered.connect(lambda _=False, z=z: self.set_zoom(z))
        sheet.menu(m, self.zoom_btn)

    def toggle_two(self):
        doc = self.doc
        if doc is None or not doc.ready:
            return
        page = self.current_page()
        doc.two = not doc.two
        self._place(doc)
        self._apply_zoom(doc, keep=False)
        self.go_to(page)
        self._update_bar()

    def toggle_cover(self):
        doc = self.doc
        if doc is None or not doc.ready:
            return
        page = self.current_page()
        doc.cover = not doc.cover
        if not doc.two:
            doc.two = True
        self._place(doc)
        self._apply_zoom(doc, keep=False)
        self.go_to(page)
        self._update_bar()

    def toggle_dark(self):
        self.dark = not self.dark
        self.dark_btn.setChecked(self.dark)
        self.c.settings.pdf_dark_pages = self.dark
        for doc in self.docs:
            for item in doc.pages:
                item.dark = self.dark
                item.pm_key = None
                item.update()
        self.view.viewport().update()
        self.view.resetCachedContent()
        self._ask_pages()
        if self.full is not None:
            self.full.ask()

    def full_screen(self):
        doc = self.doc
        if doc is None or not doc.ready:
            return
        if self.full is not None:
            self.full.end()
            return
        host = self.window()
        if self.full_widget is None or self.full_widget.host is not host:
            self.full_widget = FullScreen(self, host)
        self.full = self.full_widget
        self.full.start(self.current_page())

    def _more_menu(self):
        m = QMenu(self)
        round_menu(m)
        muted = self.c.theme.muted
        doc = self.doc
        ready = doc is not None and doc.ready
        a = m.addAction(tr("First page alone, like a book's cover"))
        a.setCheckable(True)
        a.setChecked(bool(doc and doc.cover and doc.two))
        a.setEnabled(ready)
        a.triggered.connect(self.toggle_cover)
        m.addSeparator()
        for words, icon, fn in ((tr("Arrange pages"), "grid", self.arrange_this),
                                (tr("Print (Ctrl + P)"), "document", self.print_this),
                                (tr("Show in folder"), "folder_open", self.reveal_this)):
            act = m.addAction(icons.icon(icon, muted), words)
            act.setEnabled(ready)
            act.triggered.connect(lambda _=False, f=fn: f())
        m.addSeparator()
        s = self.c.settings
        a = m.addAction(tr("Open PDFs with Rakiza Core (it shows up under \"Open with\")"))
        a.setCheckable(True)
        a.setChecked(bool(getattr(s, "open_pdfs", True)))
        a.triggered.connect(lambda on: self.c.set_open_files("open_pdfs", bool(on)))
        a = m.addAction(tr("Make Rakiza Core the default for PDFs…"))
        a.triggered.connect(self._make_default)
        sheet.menu(m, self.more_btn)

    def _make_default(self):
        from . import assoc
        if not getattr(self.c.settings, "open_pdfs", True):
            self.c.set_open_files("open_pdfs", True)
        assoc.open_default_apps()

    def edit_this(self):
        if self.doc is not None:
            self.pdf.passwords.setdefault(str(self.doc.src), self.doc.password)
            self.pdf.open_editor(self.doc.src)

    def arrange_this(self):
        if self.doc is not None:
            self.pdf.passwords.setdefault(str(self.doc.src), self.doc.password)
            self.pdf.open_board([self.doc.src], "organize")

    def print_this(self):
        if self.doc is not None and self.doc.ready:
            self.pdf.run("print", [self.doc.src])

    def reveal_this(self):
        if self.doc is not None:
            from .winutil import reveal_in_explorer
            reveal_in_explorer(self.doc.src)

    def _say(self, text: str):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    # ---- selecting and copying

    def _ordered(self, sel) -> tuple[tuple[int, int], tuple[int, int]]:
        a, b = sel
        return (a, b) if a <= b else (b, a)

    def set_selection(self, start: tuple[int, int], end: tuple[int, int]):
        doc = self.doc
        if doc is None:
            return
        doc.sel = (start, end)
        lo, hi = self._ordered(doc.sel)
        self.ask_words([p for p in range(lo[0], hi[0] + 1) if p not in doc.words])
        self._draw_selection()

    def clear_selection(self):
        doc = self.doc
        if doc is None:
            return
        doc.sel = None
        self.copy_waiting = False
        self._draw_selection()

    def _draw_selection(self):
        doc = self.doc
        if doc is None:
            return
        spans = self.selected_spans(doc)
        for item in doc.pages:
            rects = []
            if item.index in spans and item.index in doc.words:
                a, b = spans[item.index]
                rects = line_rects(doc.words[item.index], a, b)
            if rects != item.sel:
                item.sel = rects
                item.update()

    def selected_spans(self, doc: Doc) -> dict[int, tuple[int, int]]:
        """For each page in the selection, its letters selected (start, end)."""
        if not doc.sel:
            return {}
        (p1, i1), (p2, i2) = self._ordered(doc.sel)
        out = {}
        for p in range(p1, p2 + 1):
            n = len((doc.words.get(p) or {}).get("text") or "")
            a = i1 if p == p1 else 0
            b = i2 + 1 if p == p2 else n
            if p in doc.words:
                b = min(b, n)
            if b > a:
                out[p] = (a, b)
        return out

    def selected_text(self) -> str:
        doc = self.doc
        if doc is None:
            return ""
        parts = []
        for p, (a, b) in sorted(self.selected_spans(doc).items()):
            if p in doc.words:
                parts.append(text_between(doc.words[p], a, b))
        return "\n".join(part.strip("\n") for part in parts)

    def copy(self):
        doc = self.doc
        if self.find_box.hasFocus() or self.page_box.hasFocus():
            focus = self.find_box if self.find_box.hasFocus() else self.page_box
            focus.copy()
            return
        if doc is None or not doc.sel:
            return
        (p1, _), (p2, _) = self._ordered(doc.sel)
        missing = [p for p in range(p1, p2 + 1) if p not in doc.words]
        if missing:  # (the words of some pages aren't read yet: copied once they are)
            self.copy_waiting = True
            self.ask_words(missing)
            self._say(tr("Reading the pages…"))
            return
        self.copy_waiting = False
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
            self._say("")
            self.c.say(tr("Copied"), tr("{count} letters", count=len(text)))

    def select_all(self):
        doc = self.doc
        if self.find_box.hasFocus():
            self.find_box.selectAll()
            return
        if doc is None or not doc.ready or not doc.pages:
            return
        last = len(doc.pages) - 1
        end = len((doc.words.get(last) or {}).get("text") or "") or MAX_CHARS
        self.set_selection((0, 0), (last, end - 1))

    def context_menu(self, at):
        m = QMenu(self)
        round_menu(m)
        muted = self.c.theme.muted
        doc = self.doc
        a = m.addAction(icons.icon("copy", muted), tr("Copy (Ctrl + C)"))
        a.setEnabled(bool(doc and doc.sel))
        a.triggered.connect(self.copy)
        a = m.addAction(tr("Select all (Ctrl + A)"))
        a.setEnabled(bool(doc and doc.ready))
        a.triggered.connect(self.select_all)
        m.addSeparator()
        a = m.addAction(icons.icon("text_edit_style", muted), tr("Edit"))
        a.setEnabled(bool(doc and doc.ready))
        a.triggered.connect(self.edit_this)
        sheet.menu(m, at)

    # ---- links

    def follow(self, link: dict):
        kind, to = link["to"]
        if kind == "page":
            self.go_to(int(to))
            return
        url = str(to).strip()
        if not safe_address(url):
            self.c.tell(tr("Rakiza Core PDF"), tr("This link doesn't go to a web page, so it wasn't opened."))
            return
        from PySide6.QtWidgets import QMessageBox
        answer = sheet.question(self, tr("Open this link?"), tr("The PDF wants to open:\n{address}",
                                                                 address=url[:300]))
        if answer == QMessageBox.Yes:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))

    # ---- Find

    def show_find(self):
        if self.doc is None or not self.doc.ready:
            return
        self.find_bar.show()
        text = self.selected_text() if self.doc.sel else ""
        if text and "\n" not in text and len(text) < 100:
            self.find_box.setText(text)
        self.find_box.setFocus()
        self.find_box.selectAll()
        self._find_now()

    def hide_find(self):
        self.find_bar.hide()
        doc = self.doc
        if doc is not None:
            doc.matches, doc.match_at = [], -1
            for item in doc.pages:
                if item.found or item.current:
                    item.found, item.current = [], []
                    item.update()
        self.found_label.setText("")
        self.view.setFocus()

    def _find_now(self):
        doc = self.doc
        if doc is None or not doc.ready:
            return
        query = self.find_box.text()
        keep = doc.matches[doc.match_at] if 0 <= doc.match_at < len(doc.matches) else None
        found: list[tuple[int, int]] = []
        if query and self.find_bar.isVisible():
            missing = [i for i in range(len(doc.pages)) if i not in doc.words]
            if missing:
                doc.renderer.want_words(missing)
            for i in range(len(doc.pages)):
                words = doc.words.get(i)
                if words:
                    found += [(i, at) for at in find_in(words["text"], query, self.case_box.isChecked())]
        doc.matches = found
        if keep in found:
            doc.match_at = found.index(keep)
        elif found:
            here = self.current_page()
            doc.match_at = next((n for n, (p, _a) in enumerate(found) if p >= here), 0)
            if keep is None:
                self._show_match()
        else:
            doc.match_at = -1
        self._draw_found()

    def _draw_found(self):
        doc = self.doc
        if doc is None:
            return
        n = len(self.find_box.text())
        by_page: dict[int, list] = {}
        current: dict[int, list] = {}
        for k, (p, at) in enumerate(doc.matches):
            rects = line_rects(doc.words[p], at, at + n)
            (current if k == doc.match_at else by_page).setdefault(p, []).extend(rects)
        for item in doc.pages:
            f, cur = by_page.get(item.index, []), current.get(item.index, [])
            if f != item.found or cur != item.current:
                item.found, item.current = f, cur
                item.update()
        query = self.find_box.text()
        reading = len(doc.words) < len(doc.pages)
        if not query:
            text = ""
        elif doc.matches:
            text = tr("{number} of {count}", number=doc.match_at + 1, count=len(doc.matches))
        else:
            text = tr("Reading the pages…") if reading else tr("Not found")
        self.found_label.setText(text)

    def next_match(self, step: int):
        doc = self.doc
        if doc is None:
            return
        if not self.find_bar.isVisible():
            self.show_find()
            return
        if not doc.matches:
            return
        doc.match_at = (doc.match_at + step) % len(doc.matches)
        self._draw_found()
        self._show_match()

    def _show_match(self):
        doc = self.doc
        if doc is None or not 0 <= doc.match_at < len(doc.matches):
            return
        p, at = doc.matches[doc.match_at]
        rects = line_rects(doc.words[p], at, at + max(1, len(self.find_box.text())))
        if not rects:
            self.go_to(p)
            return
        r = doc.pages[p].mapRectToScene(rects[0])
        self.view.ensureVisible(r, 60, 80)
        self._update_page_box()
