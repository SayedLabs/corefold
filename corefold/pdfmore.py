"""Corefold PDF: printing a PDF, and comparing two PDFs. No windows here (pdfdialogs.py asks the questions).

Printing draws every page with PDFium at the printer's own sharpness (up to 300 dpi) and hands it to the printer
through Qt, the same way a PDF app does, so nothing else has to be installed. Comparing reads the words of both
PDFs and draws their pages side by side, and writes what changed as a new PDF: the pages next to each other with
the places that look different marked, then every word taken out or put in."""
from __future__ import annotations

import difflib
import logging
from pathlib import Path

from .core import Ctx, Result, Tool, render_scale
from .i18n import tr
from .pdftools import PDFIUM, open_pdfium, parse_ranges

log = logging.getLogger("corefold")

MAX_PRINT_DPI = 300


# ----------------------------------------------------------------------------- printing

def printers() -> tuple[list[str], str]:
    """The printers Windows knows (names), and the one it uses unless told otherwise."""
    from PySide6.QtPrintSupport import QPrinterInfo
    names = [n for n in QPrinterInfo.availablePrinterNames() if n]
    default = QPrinterInfo.defaultPrinterName() or (names[0] if names else "")
    return names, default


def can_print_both_sides(name: str) -> bool:
    from PySide6.QtGui import QPageLayout  # noqa: F401  (loads QtGui before QtPrintSupport)
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    info = QPrinterInfo.printerInfo(name)
    if info.isNull():
        return False
    return any(m in (QPrinter.DuplexLongSide, QPrinter.DuplexAuto, QPrinter.DuplexShortSide)
               for m in info.supportedDuplexModes())


def page_numbers(text: str, count: int) -> list[int]:
    """"" = every page; otherwise "1-3, 5" (pages counted from 0)."""
    if not (text or "").strip():
        return list(range(count))
    return [p for part in parse_ranges(text, count) for p in part]


def print_pdf(src: Path, ctx: Ctx) -> Result:
    """Prints the PDF on the printer picked (params: printer, copies, pages, both_sides, gray). Each page is
    fitted inside the paper's printable area, turned when it's the other way round from the paper."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter, QTransform
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    p = ctx.params
    name = p.get("printer") or QPrinterInfo.defaultPrinterName()
    info = QPrinterInfo.printerInfo(name) if name else QPrinterInfo()
    if info.isNull():
        raise RuntimeError(tr("That printer isn't there any more"))
    printer = QPrinter(info, QPrinter.HighResolution)
    printer.setDocName(src.name)
    printer.setCopyCount(max(1, min(99, int(p.get("copies", 1) or 1))))
    printer.setColorMode(QPrinter.GrayScale if p.get("gray") else QPrinter.Color)
    if p.get("both_sides"):
        printer.setDuplex(QPrinter.DuplexAuto)
    if p.get("to_file"):  # (the tests: Windows' own "Microsoft Print to PDF" writes here instead of asking)
        printer.setOutputFileName(str(p["to_file"]))
    password = p.get("password", "")
    with PDFIUM:
        doc = open_pdfium(src, password)
        count = len(doc)
    try:
        try:
            pages = page_numbers(p.get("pages", ""), count)
        except ValueError as e:
            raise RuntimeError(str(e)) from None
        dpi = min(MAX_PRINT_DPI, printer.resolution() or MAX_PRINT_DPI)
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError(tr("The printer couldn't be started"))
        try:
            for n, i in enumerate(pages):
                ctx.check()
                if n:
                    printer.newPage()
                area = printer.pageLayout().paintRectPixels(printer.resolution())
                with PDFIUM:
                    page = doc[i]
                    try:
                        w, h = page.get_width(), page.get_height()
                        scale = render_scale(w, h, dpi / 72)
                        pil = page.render(scale=scale, may_draw_forms=True, grayscale=bool(p.get("gray"))).to_pil()
                    finally:
                        page.close()
                pil = pil.convert("RGB")
                img = QImage(pil.tobytes(), pil.width, pil.height, pil.width * 3, QImage.Format_RGB888).copy()
                if (img.width() > img.height()) != (area.width() > area.height()):
                    img = img.transformed(QTransform().rotate(90))
                box = QRectF(0, 0, area.width(), area.height())
                fit = min(box.width() / img.width(), box.height() / img.height())
                tw, th = img.width() * fit, img.height() * fit
                target = QRectF((box.width() - tw) / 2, (box.height() - th) / 2, tw, th)
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                painter.drawImage(target, img)
                ctx.progress((n + 1) / len(pages))
        except BaseException:
            printer.abort()
            raise
        finally:
            if painter.isActive():
                painter.end()
    finally:
        with PDFIUM:
            doc.close()
    return Result(note=tr("Sent to {printer}. Pages: {count}", count=len(pages), printer=name))


PRINT = Tool("pdftool:print", tr("Print"), tr("Print on a printer"), print_pdf, password=True, renames=False)


# ----------------------------------------------------------------------------- comparing two PDFs

COMPARE_DPI = 96
CELL = 12          # how finely the pages are looked over for differences (pixels at COMPARE_DPI)
INK_DIFF = 48      # how different a dot must be to count (0-255): leaves out tiny shading changes
MAX_WORD_LINES = 4000


def _words(doc, i: int) -> list[str]:
    page = doc[i]
    try:
        tp = page.get_textpage()
        try:
            text = tp.get_text_bounded()
        finally:
            tp.close()
    finally:
        page.close()
    return text.split()


def _picture(doc, i: int, width: int):
    """Page i drawn in grey, width pixels wide (both PDFs' pages at the same width, so they line up)."""
    page = doc[i]
    try:
        w = page.get_width()
        pil = page.render(scale=width / max(1.0, w), may_draw_forms=True).to_pil()
    finally:
        page.close()
    return pil.convert("RGB")


def changed_boxes(a, b) -> list[tuple[int, int, int, int]]:
    """Where two pictures of a page look different: boxes (x, y, w, h), in the pictures' pixels. The pictures
    are looked over in small squares; squares that differ and touch are joined into one box."""
    import numpy as np
    w, h = max(a.width, b.width), max(a.height, b.height)
    ga = np.full((h, w), 255, np.int16)
    gb = np.full((h, w), 255, np.int16)
    ga[:a.height, :a.width] = np.asarray(a.convert("L"), np.int16)
    gb[:b.height, :b.width] = np.asarray(b.convert("L"), np.int16)
    diff = np.abs(ga - gb) > INK_DIFF
    rows, cols = -(-h // CELL), -(-w // CELL)
    pad = np.zeros((rows * CELL, cols * CELL), bool)
    pad[:h, :w] = diff
    cells = pad.reshape(rows, CELL, cols, CELL).sum(axis=(1, 3)) >= 3
    seen = np.zeros_like(cells)
    boxes = []
    for r0, c0 in zip(*np.nonzero(cells)):
        if seen[r0, c0]:
            continue
        stack, r1, r2, c1, c2 = [(r0, c0)], r0, r0, c0, c0
        seen[r0, c0] = True
        while stack:
            r, c = stack.pop()
            r1, r2, c1, c2 = min(r1, r), max(r2, r), min(c1, c), max(c2, c)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < rows and 0 <= cc < cols and cells[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
        boxes.append((int(c1 * CELL), int(r1 * CELL), int((c2 - c1 + 1) * CELL), int((r2 - r1 + 1) * CELL)))
    return boxes


def word_changes(old: list[list[str]], new: list[list[str]]) -> list[tuple[str, int, int, str]]:
    """What changed in the words, in reading order: (kind, old page, new page, the words), kind = "out" (only
    in the first PDF), "in" (only in the second) or "swap" (words became other words: "old → new"). Pages are
    counted from 1; 0 = no such page."""
    def flat(pages):
        words, where = [], []
        for n, ws in enumerate(pages, 1):
            words += ws
            where += [n] * len(ws)
        return words, where
    a, wa = flat(old)
    b, wb = flat(new)
    out = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        pa = wa[i1] if i1 < len(wa) else (wa[-1] if wa else 0)
        pb = wb[j1] if j1 < len(wb) else (wb[-1] if wb else 0)
        if op == "delete":
            out.append(("out", pa, pb, " ".join(a[i1:i2])))
        elif op == "insert":
            out.append(("in", pa, pb, " ".join(b[j1:j2])))
        else:
            out.append(("swap", pa, pb, " ".join(a[i1:i2]) + "  →  " + " ".join(b[j1:j2])))
    return out


def compare_pdfs(files: list[Path], ctx: Ctx) -> Result:
    """Two PDFs compared: a new PDF with their pages side by side (what looks different marked in both) and a
    list of every word taken out or put in. The first PDF is the older one."""
    from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
    if len(files) != 2:
        raise RuntimeError(tr("Pick two PDFs to compare"))
    first, second = files
    with PDFIUM:
        try:
            da = open_pdfium(first)
        except Exception:
            raise RuntimeError(tr("{name} can't be opened: if it has a password, unlock it first",
                                  name=first.name)) from None
        try:
            db = open_pdfium(second)
        except Exception:
            da.close()
            raise RuntimeError(tr("{name} can't be opened: if it has a password, unlock it first",
                                  name=second.name)) from None
    out = ctx.out_path(first, f"{ctx.stem(first)} vs {ctx.stem(second)} (compared).pdf")
    try:
        with PDFIUM:
            na, nb = len(da), len(db)
            old = [_words(da, i) for i in range(na)]
            new = [_words(db, i) for i in range(nb)]
        ctx.progress(0.1)
        changes = word_changes(old, new)
        writer = QPdfWriter(str(out))
        writer.setResolution(COMPARE_DPI)
        writer.setTitle(tr("{first} compared with {second}", first=first.name, second=second.name))
        writer.setCreator("Corefold")
        writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Landscape, QMarginsF(10, 10, 10, 10),
                                         QPageLayout.Millimeter))
        painter = QPainter()
        if not painter.begin(writer):
            raise RuntimeError(tr("Couldn't save the comparison"))
        red, green, ink, soft = QColor(220, 50, 60), QColor(30, 150, 80), QColor(30, 30, 30), QColor(110, 110, 110)
        try:
            area = writer.pageLayout().paintRectPixels(COMPARE_DPI)
            W, H = area.width(), area.height()
            head = QFont("Segoe UI", 11, QFont.DemiBold)
            body = QFont("Segoe UI", 9)
            # first page: the summary
            pages = max(na, nb)
            col = (W - 30) // 2
            changed = []  # (page, places that look different): pictures are drawn again when they're put in
            for i in range(pages):
                ctx.check()
                if i < na and i < nb:
                    with PDFIUM:
                        pa, pb = _picture(da, i, col), _picture(db, i, col)
                    boxes = changed_boxes(pa, pb)
                    del pa, pb
                    if boxes:
                        changed.append((i, boxes))
                else:
                    changed.append((i, []))
                ctx.progress(0.1 + 0.5 * (i + 1) / max(1, pages))
            painter.setPen(ink)
            painter.setFont(QFont("Segoe UI", 16, QFont.DemiBold))
            painter.drawText(QRectF(0, 0, W, 40), Qt.AlignLeft | Qt.AlignVCenter, tr("What changed"))
            painter.setFont(body)
            lines = [tr("First (older): {name}. Pages: {count}", name=first.name, count=na),
                     tr("Second (newer): {name}. Pages: {count}", name=second.name, count=nb),
                     tr("Pages that look different: {count}", count=len(changed)),
                     tr("Words taken out: {count}", count=sum(len(c[3].split()) for c in changes if c[0] == "out")),
                     tr("Words put in: {count}", count=sum(len(c[3].split()) for c in changes if c[0] == "in")),
                     tr("Places where words were changed: {count}", count=sum(c[0] == "swap" for c in changes))]
            if not changes and not changed:
                lines.append(tr("The two PDFs look the same and have the same words."))
            y = 50
            for line in lines:
                painter.drawText(QRectF(0, y, W, 22), Qt.AlignLeft | Qt.AlignVCenter, line)
                y += 24
            painter.setPen(soft)
            painter.drawText(QRectF(0, y + 6, W, 40), Qt.TextWordWrap,
                             tr("Red boxes mark what looks different on a page. The words that changed are listed "
                                "after the pages."))
            # the pages that differ, side by side
            for n_done, (i, boxes) in enumerate(changed):
                ctx.check()
                with PDFIUM:
                    pa = _picture(da, i, col) if i < na else None
                    pb = _picture(db, i, col) if i < nb else None
                ctx.progress(0.6 + 0.3 * (n_done + 1) / len(changed))
                writer.newPage()
                painter.setPen(ink)
                painter.setFont(head)
                title = tr("Page {number}", number=i + 1)
                if pa is None:
                    title += " · " + tr("only in the second PDF")
                elif pb is None:
                    title += " · " + tr("only in the first PDF")
                else:
                    title += " · " + tr("Places that look different: {count}", count=len(boxes))
                painter.drawText(QRectF(0, 0, W, 26), Qt.AlignLeft | Qt.AlignVCenter, title)
                top = 32
                room = H - top
                for n, (pic, name) in enumerate(((pa, first.name), (pb, second.name))):
                    x = n * (col + 30)
                    painter.setPen(soft)
                    painter.setFont(body)
                    painter.drawText(QRectF(x, top, col, 18), Qt.AlignLeft | Qt.AlignVCenter, name)
                    if pic is None:
                        continue
                    img = QImage(pic.tobytes(), pic.width, pic.height, pic.width * 3, QImage.Format_RGB888).copy()
                    fit = min(1.0, (room - 22) / img.height(), col / img.width())
                    target = QRectF(x, top + 22, img.width() * fit, img.height() * fit)
                    painter.drawImage(target, img)
                    painter.setPen(QPen(QColor(200, 200, 200), 1))
                    painter.drawRect(target)
                    painter.setPen(QPen(red, 2))
                    for bx, by, bw, bh in boxes:
                        painter.drawRect(QRectF(target.x() + bx * fit, target.y() + by * fit, bw * fit, bh * fit))
            # the words
            if changes:
                writer.newPage()
                y = 0
                painter.setPen(ink)
                painter.setFont(head)
                painter.drawText(QRectF(0, y, W, 26), Qt.AlignLeft | Qt.AlignVCenter, tr("Words that changed"))
                y += 32
                painter.setFont(body)
                shown = changes[:MAX_WORD_LINES]
                for kind, a, b, words in shown:
                    ctx.check()
                    sign = {"out": "−", "in": "+", "swap": "~"}[kind]
                    where = tr("page {first} / {second}", first=a or "–", second=b or "–")
                    text = f"{sign}  {where}:  {words}"
                    rect = painter.boundingRect(QRectF(0, 0, W, 10_000), Qt.TextWordWrap, text)
                    if y + rect.height() > H:
                        writer.newPage()
                        y = 0
                    painter.setPen({"out": red, "in": green, "swap": ink}[kind])
                    painter.drawText(QRectF(0, y, W, rect.height()), Qt.TextWordWrap, text)
                    y += rect.height() + 4
                if len(changes) > len(shown):
                    painter.setPen(soft)
                    painter.drawText(QPointF(0, min(H - 4, y + 14)),
                                     tr("…and {count} more", count=len(changes) - len(shown)))
        finally:
            painter.end()
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        with PDFIUM:
            da.close()
            db.close()
    ctx.progress(1.0)
    if not changes and not changed:
        note = tr("The two PDFs look the same and have the same words.")
    else:
        note = tr("Pages that look different: {count}. Changes in the words: {words}", count=len(changed),
                  words=len(changes))
    return Result([out], note=note)


COMPARE = Tool("pdftool:compare", tr("Compare"), tr("Compare two PDFs"), compare_pdfs, batch=True, renames=False)


# ----------------------------------------------------------------------------- bookmarks

MAX_BOOKMARKS = 5000  # (a list longer than this is shown cut: nobody arranges more by hand)


def read_bookmarks(src: Path, password: str = "") -> tuple[list[list], int, int]:
    """The PDF's bookmarks (the list of chapters a reader shows on the side) as [level, title, page] (level from
    0, page counted from 0), its number of pages, and how many bookmarks open something other than one of its
    own pages (a web page, another file): those can't be kept when the list is saved again."""
    from .pdftools import open_reader
    reader = open_reader(src, password)
    found: list[list] = []
    other = [0]

    def walk(items, level):
        for item in items:
            if len(found) >= MAX_BOOKMARKS:
                return
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            try:
                page = reader.get_destination_page_number(item)
            except Exception:
                page = None
            title = str(getattr(item, "title", "") or "").strip()
            if page is None or page < 0:
                other[0] += 1
                continue
            found.append([level, title or tr("Page {number}", number=page + 1), page])
    try:
        walk(reader.outline, 0)
    except Exception:
        log.debug("the bookmarks of %s couldn't all be read", src, exc_info=True)
    # (a list that starts deeper than the top, or skips a level, is made even, so each one has its parent)
    level = -1
    for mark in found:
        mark[0] = max(0, min(mark[0], level + 1))
        level = mark[0]
    return found, len(reader.pages), other[0]


def write_bookmarks(src: Path, ctx: Ctx) -> Result:
    """A new copy of the PDF with this list of bookmarks (params "marks": [level, title, page] in order),
    shown on the side when it's opened. An empty list takes them all away."""
    from pypdf import PdfWriter
    from pypdf.generic import NameObject

    from .pdftools import open_reader, write
    password = ctx.params.get("password", "")
    reader = open_reader(src, password)
    count = len(reader.pages)
    writer = PdfWriter(clone_from=reader)
    root = writer._root_object
    if "/Outlines" in root:
        del root[NameObject("/Outlines")]
    parents: list = []
    marks = [m for m in (ctx.params.get("marks") or []) if isinstance(m, (list, tuple)) and len(m) == 3]
    for n, (level, title, page) in enumerate(marks[:MAX_BOOKMARKS]):
        ctx.check()
        level = max(0, min(int(level), len(parents)))
        page = max(0, min(int(page), count - 1))
        parent = parents[level - 1] if level else None
        item = writer.add_outline_item(str(title).strip() or tr("Page {number}", number=page + 1), page,
                                       parent=parent)
        del parents[level:]
        parents.append(item)
        ctx.progress(0.8 * (n + 1) / max(1, len(marks)))
    if marks:
        writer.page_mode = "/UseOutlines"  # (readers open it with the list showing)
    elif root.get("/PageMode") == "/UseOutlines":
        del root[NameObject("/PageMode")]
    out = ctx.out_path(src, f"{ctx.stem(src)} (bookmarks).pdf")
    write(writer, out, password)
    return Result([out], note=tr("{count} bookmarks", count=len(marks)) if marks else tr("Bookmarks taken away"))


BOOKMARKS = Tool("pdftool:bookmarks", tr("Bookmarks"), tr("Change the list of chapters"), write_bookmarks,
                 password=True, renames=False)
