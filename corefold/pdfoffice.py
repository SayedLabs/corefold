"""Rakiza Core PDF: PDF to Excel and PDF to PowerPoint, written by hand (no Office, no extra library).

Excel: the words of each page are put in rows (by where they sit) and columns (by where they start, the
same for the whole page), so a table in the PDF comes out as a table; numbers come out as numbers. Each
page is a sheet of its own; a page that's mostly Arabic reads right to left.

PowerPoint: each page is a slide, as a sharp picture filling it."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .core import Ctx, Result, Tool
from .i18n import tr

MAX_SHEET_ROWS = 20000        # (a page with more lines than this is very rare: the rest is left out)
COLUMN_SNAP = 8.0             # cells that start this close (in points) are in the same column
SLIDE_DPI = 150               # the pictures on the slides
_NUMBER = re.compile(r"^[-+(]?\d{1,3}(?:[,٬]\d{3})*(?:[.٫]\d+)?\)?%?$|^[-+(]?\d+(?:[.٫]\d+)?\)?%?$")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC = re.compile("[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _x(text: str) -> str:
    """Text that can go in an XML file (letters XML can't hold are left out)."""
    return escape(_BAD_XML.sub("", text), {'"': "&quot;"})


# ----------------------------------------------------------------------------- the words of a page, in a table

def page_words(words: dict) -> list[tuple[str, float, float, float, float]]:
    """The page's words, (text, x, y, w, h) each, from pdfreader.page_words (letters and where each is)."""
    from .pdfedit import arabic_letters
    text, boxes = words.get("text") or "", words.get("boxes") or []
    out, cur, box = [], [], None

    def flush():
        nonlocal cur, box
        if cur and box is not None:
            out.append((arabic_letters("".join(cur)), box[0], box[1], box[2] - box[0], box[3] - box[1]))
        cur, box = [], None
    for ch, b in zip(text, boxes):
        if b is None or ch.isspace() or ch in "\ufffe\x02":
            continue  # (a space or a line break counts only where there's room between the letters: PDFium adds
            # ones of its own between letters placed one by one)
        x, y, w, h = b
        if box is not None and (x > box[2] + max(1.0, h * 0.16) or x + w < box[0] - max(1.0, h * 0.16)
                                or abs((y + h / 2) - (box[1] + box[3]) / 2) > h * 0.6):
            flush()  # (room between them, or the next line: a word of its own)
        cur.append(ch)
        box = [x, y, x + w, y + h] if box is None else [min(box[0], x), min(box[1], y), max(box[2], x + w),
                                                        max(box[3], y + h)]
    flush()
    return out


def table(words: list[tuple[str, float, float, float, float]]) -> tuple[list[list[str]], bool]:
    """The words in rows and columns (a list of rows, each a list of cells, "" where nothing is), and
    whether the page reads right to left (mostly Arabic: its first column is the one on the right)."""
    if not words:
        return [], False
    rtl = sum(1 for w in words if _ARABIC.search(w[0])) > len(words) / 2
    # rows: words whose middles are close enough, top to bottom
    rows: list[list] = []
    for w in sorted(words, key=lambda w: (w[2] + w[4] / 2, w[1])):
        mid = w[2] + w[4] / 2
        if rows and abs(mid - rows[-1][0]) <= max(2.0, min(w[4], rows[-1][1]) * 0.5):
            rows[-1][2].append(w)
        else:
            rows.append([mid, w[4], [w]])
    # cells: the words of a row close to each other are one cell
    cell_rows = []
    for _mid, _h, ws in rows[:MAX_SHEET_ROWS]:
        ws.sort(key=lambda w: -(w[1] + w[3]) if rtl else w[1])
        cells = []
        for w in ws:
            text, x, _y, wd, h = w
            if cells:
                last = cells[-1]
                gap = (last["x0"] - (x + wd)) if rtl else (x - last["x1"])
                if gap < h * 0.9:  # (one space or so: the same cell)
                    last["words"].append(text)
                    last["x0"], last["x1"] = min(last["x0"], x), max(last["x1"], x + wd)
                    continue
            cells.append({"words": [text], "x0": x, "x1": x + wd})
        cell_rows.append(cells)
    # columns: where cells start (their right edge, right to left), the same for the whole page
    starts = sorted({round(-c["x1"] if rtl else c["x0"], 1) for cells in cell_rows for c in cells})
    columns: list[float] = []
    for s in starts:
        if not columns or s - columns[-1] > COLUMN_SNAP:
            columns.append(s)
    out = []
    for cells in cell_rows:
        row = [""] * len(columns)
        for c in cells:
            s = -c["x1"] if rtl else c["x0"]
            k = max(i for i, col in enumerate(columns) if col <= s + COLUMN_SNAP)
            while k < len(row) and row[k]:
                k += 1  # (two cells in the same column of a row: the next one goes after it)
            if k >= len(row):
                row.append("")
            row[k] = " ".join(c["words"])
        out.append(row)
    return out, rtl


def number(text: str):
    """The number written (1,234.50 or ١٢٣٫٥ or 15%), or None when it's words."""
    t = text.strip().translate(_DIGITS)
    if not t or not _NUMBER.match(t):
        return None
    if len(t) > 1 and t[0] == "0" and t[1].isdigit():
        return None  # (0551234567, 007: a phone or a code, its 0 kept)
    neg = t.startswith("(") and t.endswith(")")
    pct = t.endswith("%")
    t = t.strip("()%+").replace(",", "").replace("٬", "").replace("٫", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    v = -v if neg else v
    return v / 100 if pct else v


# ----------------------------------------------------------------------------- an Excel file (.xlsx)

def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_xml(rows: list[list[str]], rtl: bool) -> str:
    widths: dict[int, int] = {}
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, text in enumerate(row):
            if not text:
                continue
            widths[c] = max(widths.get(c, 0), min(60, len(text)))
            ref = f"{_col(c)}{r}"
            v = number(text)
            if v is not None:
                cells.append(f'<c r="{ref}"><v>{v:.15g}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_x(text)}</t></is></c>')
        if cells:
            body.append(f'<row r="{r}">{"".join(cells)}</row>')
    rtl_view = ' rightToLeft="1"' if rtl else ""
    cols = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{max(8, w + 2)}" customWidth="1"/>'
                   for c, w in sorted(widths.items()))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetViews><sheetView workbookViewId="0"{rtl_view}/></sheetViews>'
            + (f"<cols>{cols}</cols>" if cols else "") +
            f'<sheetData>{"".join(body)}</sheetData></worksheet>')


def write_xlsx(out: Path, sheets: list[tuple[str, list[list[str]], bool]]):
    """An Excel workbook: sheets of (name, rows, right to left)."""
    if not sheets:
        sheets = [("Sheet1", [], False)]
    names, used = [], set()
    for name, _rows, _rtl in sheets:
        n = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Sheet"
        base, k = n, 2
        while n.lower() in used:
            n = f"{base[:27]} ({k})"
            k += 1
        used.add(n.lower())
        names.append(n)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for i in range(1, len(sheets) + 1)) + '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          + "".join(f'<sheet name="{_x(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, start=1))
          + '</sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                         f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                         for i in range(1, len(sheets) + 1))
               + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/'
               '2006/relationships/styles" Target="styles.xml"/></Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill>'
              '<fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              '</styleSheet>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        for i, (_name, rows, rtl) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows, rtl))


def to_excel(src: Path, ctx: Ctx) -> Result:
    from . import pdfreader, pdftools
    password = ctx.params.get("password", "") or ""
    sheets = []
    found_words = False
    with pdftools.PDFIUM:
        doc = pdftools.open_pdfium(src, password)
    try:
        count = len(doc)
        for i in range(count):
            ctx.check()
            with pdftools.PDFIUM:
                words = pdfreader.page_words(doc, i)
            rows, rtl = table(page_words(words))
            found_words = found_words or bool(rows)
            sheets.append((tr("Page {number}", number=i + 1), rows, rtl))
            ctx.progress(0.9 * (i + 1) / max(1, count))
    finally:
        with pdftools.PDFIUM:
            doc.close()
    if not found_words:
        raise RuntimeError(tr("This PDF has no words of its own (scanned pages?): make it searchable first."))
    out = ctx.out_path(src, f"{ctx.stem(src)}.xlsx")
    write_xlsx(out, sheets)
    return Result([out])


# ----------------------------------------------------------------------------- a PowerPoint file (.pptx)

EMU = 12700  # (in a point)
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _rels(items: list[tuple[str, str, str]]) -> str:
    return (_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="{i}" Type="{_REL}/{t}" Target="{target}"/>' for i, t, target in items)
            + "</Relationships>")


_THEME = (_HEAD + f'<a:theme xmlns:a="{_A}" name="Rakiza"><a:themeElements>'
          '<a:clrScheme name="Rakiza"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
          '<a:dk2><a:srgbClr val="1F2937"/></a:dk2><a:lt2><a:srgbClr val="F3F4F6"/></a:lt2>'
          '<a:accent1><a:srgbClr val="1F3A93"/></a:accent1><a:accent2><a:srgbClr val="D32F2F"/></a:accent2>'
          '<a:accent3><a:srgbClr val="2E7D32"/></a:accent3><a:accent4><a:srgbClr val="F57C00"/></a:accent4>'
          '<a:accent5><a:srgbClr val="7B1FA2"/></a:accent5><a:accent6><a:srgbClr val="00838F"/></a:accent6>'
          '<a:hlink><a:srgbClr val="1F3A93"/></a:hlink><a:folHlink><a:srgbClr val="7B1FA2"/></a:folHlink></a:clrScheme>'
          '<a:fontScheme name="Rakiza"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
          '</a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
          '</a:fontScheme><a:fmtScheme name="Rakiza"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
          '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
          '</a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
          '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill>'
          '<a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle>'
          '<a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/>'
          '</a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
          '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
          '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')

_EMPTY_TREE = ('<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
               '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
               '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>')
_MAP = ('<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
        'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>')


def _slide_xml(n: int, cx: int, cy: int) -> str:
    return (_HEAD + f'<p:sld xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
            '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            f'<p:pic><p:nvPicPr><p:cNvPr id="2" name="Page {n}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/>'
            '</p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/>'
            f'</a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic></p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


def write_pptx(out: Path, slides: list[tuple[bytes, str, str]], size: tuple[float, float]):
    """A PowerPoint file: one slide for each (picture, "png" or "jpeg"), all of size (w, h) in points (only the
    parts PowerPoint needs, kept as plain as its own empty file)."""
    cx, cy = max(914400, int(size[0] * EMU)), max(914400, int(size[1] * EMU))
    n = len(slides)
    ct = (_HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Default Extension="png" ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/>'
          '<Override PartName="/ppt/presentation.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
          '<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
          '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
          '<Override PartName="/ppt/theme/theme1.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
          + "".join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/'
                    'vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1, n + 1))
          + '</Types>')
    pres = (_HEAD + f'<p:presentation xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:sldIdLst>' + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, n + 1))
            + f'</p:sldIdLst><p:sldSz cx="{cx}" cy="{cy}"/><p:notesSz cx="6858000" cy="9144000"/>'
            '<p:defaultTextStyle/></p:presentation>')
    pres_rels = _rels([("rId1", "slideMaster", "slideMasters/slideMaster1.xml")]
                      + [(f"rId{i + 1}", "slide", f"slides/slide{i}.xml") for i in range(1, n + 1)]
                      + [(f"rId{n + 2}", "theme", "theme/theme1.xml")])
    master = (_HEAD + f'<p:sldMaster xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">' + _EMPTY_TREE + _MAP
              + '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
              '<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>')
    layout = (_HEAD + f'<p:sldLayout xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}" type="blank" preserve="1">'
              + _EMPTY_TREE.replace("<p:cSld>", '<p:cSld name="Blank">')
              + '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", _rels([("rId1", "officeDocument", "ppt/presentation.xml")]))
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        z.writestr("ppt/slideMasters/slideMaster1.xml", master)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels",
                   _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                          ("rId2", "theme", "../theme/theme1.xml")]))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels",
                   _rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]))
        z.writestr("ppt/theme/theme1.xml", _THEME)
        for i, (data, kind) in enumerate(slides, start=1):
            ext = "jpeg" if kind == "jpeg" else "png"
            z.writestr(f"ppt/media/page{i}.{ext}", data, compress_type=zipfile.ZIP_STORED)
            z.writestr(f"ppt/slides/slide{i}.xml", _slide_xml(i, cx, cy))
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels",
                       _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                              ("rId2", "image", f"../media/page{i}.{ext}")]))


def to_powerpoint(src: Path, ctx: Ctx) -> Result:
    from . import pdftools
    password = ctx.params.get("password", "") or ""
    slides, size = [], None
    with pdftools.PDFIUM:
        doc = pdftools.open_pdfium(src, password)
    try:
        count = len(doc)
        for i in range(count):
            ctx.check()
            with pdftools.PDFIUM:
                page = doc[i]
                w, h = page.get_size()
                if page.get_rotation() % 180:
                    w, h = h, w
                size = size or (w, h)
                # (each page fills a slide of the first page's shape)
                scale = SLIDE_DPI / 72
                im = page.render(scale=scale).to_pil().convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90, optimize=True)  # (pages are mostly photos or text: small enough)
            slides.append((buf.getvalue(), "jpeg"))
            ctx.progress(0.9 * (i + 1) / max(1, count))
    finally:
        with pdftools.PDFIUM:
            doc.close()
    out = ctx.out_path(src, f"{ctx.stem(src)}.pptx")
    write_pptx(out, slides, size or (720.0, 540.0))
    return Result([out])


TO_EXCEL = Tool("pdftool:toexcel", tr("PDF to Excel"), tr("Tables and words as a spreadsheet"), to_excel,
                password=True, renames=False)
TO_POWERPOINT = Tool("pdftool:topowerpoint", tr("PDF to PowerPoint"), tr("Every page as a slide"), to_powerpoint,
                     password=True, renames=False)
