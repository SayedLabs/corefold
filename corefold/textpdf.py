"""Text files to PDF: plain text, Markdown, web pages (HTML) and tables (CSV), with no Office needed.

Qt lays the text out (Arabic joined and right to left, any language Windows has a font for) and writes
the PDF with the fonts inside it. A web page loads nothing from the internet or another computer: only
pictures inside it or next to it."""
from __future__ import annotations

import base64
import csv
import html as html_lib
import io
from pathlib import Path

from .core import TEXT_EXT, Ctx, Result
from .i18n import tr

TEXT_MAX = 10 << 20   # bigger than this is a log or data dump, not something to print
PICTURE_MAX = 30 << 20
CSV_ROWS = 20000      # (a longer table is cut there: thousands of pages nobody reads)


def is_text(p: Path) -> bool:
    return p.suffix.lower() in TEXT_EXT


def check_size(files: list[Path]) -> str | None:
    try:
        if any(is_text(f) and f.stat().st_size > TEXT_MAX for f in files):
            return tr("Too big to turn into a PDF")
    except OSError:
        pass
    return None


def _document(src: Path):
    """A QTextDocument that only loads pictures written inside the page or kept in its folder."""
    from PySide6.QtGui import QTextDocument

    from .safetext import blocked, raster_picture
    folder = src.parent.resolve()
    local = not str(folder).startswith(("\\\\", "//"))  # (a shared folder: nothing is loaded from it)

    class Document(QTextDocument):
        def loadResource(self, kind, url):
            if kind != QTextDocument.ImageResource:
                return blocked(kind)
            try:
                text = url.toString()
                if text.startswith("data:") and ";base64," in text[:80]:
                    return raster_picture(base64.b64decode(text.split(",", 1)[1])[:PICTURE_MAX])
                if not local or url.scheme() not in ("", "file") or url.host():
                    return blocked(kind)
                path = (folder / url.path()).resolve() if not url.isLocalFile() else Path(url.toLocalFile()).resolve()
                if folder not in path.parents or not path.is_file() or path.stat().st_size > PICTURE_MAX:
                    return blocked(kind)
                return raster_picture(path.read_bytes())
            except (OSError, ValueError):
                return blocked(kind)
    return Document()


def _table(text: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i >= CSV_ROWS:
            break
        cell = "th" if i == 0 else "td"
        rows.append("<tr>" + "".join(f"<{cell}>{html_lib.escape(c)}</{cell}>" for c in row) + "</tr>")
    return ("<table border='1' cellspacing='0' cellpadding='4' style='border-color:#999'>"
            + "".join(rows) + "</table>")


def text_to_pdf(src: Path, ctx: Ctx) -> Result:
    from PySide6.QtCore import QLocale, QMarginsF, QSizeF
    from PySide6.QtGui import QFont, QFontDatabase, QPageLayout, QPageSize, QPdfWriter, QTextOption

    from .peekview import decode_text
    if src.stat().st_size > TEXT_MAX:
        raise RuntimeError(tr("Too big to turn into a PDF"))
    text = decode_text(src.read_bytes())
    ctx.progress(0.1)
    doc = _document(src)
    ext = src.suffix.lower()
    font = QFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))
    font.setPointSizeF(10.5)
    if ext == ".txt":
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)  # (plain text often lines things up with spaces)
        font.setPointSizeF(9.5)
    doc.setDefaultFont(font)
    option = QTextOption(doc.defaultTextOption())
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)  # (a long line without spaces still fits)
    doc.setDefaultTextOption(option)
    doc.setDefaultStyleSheet("table { border-collapse: collapse; } th, td { padding: 3px 8px; }")
    if ext in (".html", ".htm"):
        doc.setHtml(text)
    elif ext in (".md", ".markdown"):
        doc.setMarkdown(text)
    elif ext == ".csv":
        doc.setHtml(_table(text))
    else:
        doc.setPlainText(text)
    ctx.check()
    ctx.progress(0.4)
    out = ctx.out_path(src, ctx.stem(src) + ".pdf")
    writer = QPdfWriter(str(out))
    us = QLocale().measurementSystem() == QLocale.ImperialUSSystem
    writer.setPageSize(QPageSize(QPageSize.Letter if us else QPageSize.A4))
    writer.setPageMargins(QMarginsF(18, 18, 18, 18), QPageLayout.Millimeter)
    writer.setResolution(110)
    writer.setTitle(src.stem)
    writer.setCreator("")
    doc.setPageSize(QSizeF(writer.width(), writer.height()))
    doc.print_(writer)
    del writer  # (the file is finished when the writer goes)
    ctx.progress(1.0)
    return Result([out])
