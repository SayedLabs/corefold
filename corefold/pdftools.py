"""Corefold PDF's work: putting pages from one or more PDFs together, splitting, taking pages out,
shrinking, pictures to PDF and back. No GUI code here, so it can be tested on its own.

Everything is done on this PC with pypdf (BSD) and PDFium (pypdfium2, Apache/BSD). The original
files are never changed: every result is a new file next to them."""
from __future__ import annotations

import io
import logging
import os
import re
import threading
from pathlib import Path

from .core import Ctx, Result, Tool, safe_name
from .i18n import tr

log = logging.getLogger("corefold")

# how hard "Compress" squeezes the pictures inside: (name, JPEG quality, longest side in pixels)
COMPRESS_LEVELS = {"light": ("A little (best quality)", 82, 2600),
                   "normal": ("Recommended", 65, 1800),
                   "strong": ("As small as possible", 42, 1200)}
# pictures to PDF: the page each picture goes on (width, height in points; None = the picture's own size)
PAGE_SIZES = {"picture": ("Same as the picture", None), "a4": ("A4", (595.28, 841.89)),
              "letter": ("Letter", (612.0, 792.0))}
MARGINS = {"none": ("No margin", 0), "small": ("Small margin", 18), "big": ("Big margin", 42)}
IMAGE_FORMATS = {"png": "PNG", "jpg": "JPG"}
DPIS = {100: "Screen (100 dpi)", 150: "Good (150 dpi)", 300: "Print (300 dpi)"}
SPLIT_MODES = {"each": "One PDF per page", "every": "Every few pages", "ranges": "The parts I type"}
# PDFium can't do two things at once: every use of it (page pictures, previews, saving pictures) takes turns
PDFIUM = threading.RLock()
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


# ----------------------------------------------------------------------------- page lists ("1-3, 5, 8-")

def parse_ranges(text: str, count: int) -> list[list[int]]:
    """"1-3, 5, 8-" -> [[0, 1, 2], [4], [7, … last]] (pages counted from 0). "5-3" goes backwards.
    Raises ValueError with words a person can read."""
    text = (text or "").translate(_ARABIC_DIGITS).replace("–", "-").replace("—", "-").replace("،", ",")
    text = re.sub(r"\s*-\s*", "-", text.strip())
    parts = [p for p in re.split(r"[,;\s]+", text) if p]
    if not parts:
        raise ValueError(tr("Type the pages, like 1-3, 5"))
    out = []
    for part in parts:
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part)
        if not m or (m.group(3) is None and not m.group(1) and not m.group(2)):
            raise ValueError(tr("\"{text}\" isn't a page or pages", text=part))
        if m.group(3) is not None:
            a = b = int(m.group(3))
        else:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else count
        for n in (a, b):
            if not 1 <= n <= count:
                raise ValueError(tr("There's no page {number}: this PDF has {count} pages", number=n, count=count))
        step = 1 if b >= a else -1
        out.append(list(range(a - 1, b - 1 + step, step)))
    return out


def ranges_text(pages: list[int]) -> str:
    """[0, 1, 2, 4] -> "1-3, 5" (for file names and hints)."""
    bits, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        bits.append(f"{pages[i] + 1}" if i == j else f"{pages[i] + 1}-{pages[j] + 1}")
        i = j + 1
    return ", ".join(bits)


# ----------------------------------------------------------------------------- reading and writing

def open_reader(src: Path, password: str = ""):
    """The PDF, unlocked with the password if it has one (the app asks for it when it's missing)."""
    from pypdf import PdfReader
    from .archives import NeedPassword
    reader = PdfReader(str(src))
    if reader.is_encrypted and not reader.decrypt(password or ""):
        raise NeedPassword()
    return reader


def page_count(src: Path, password: str = "") -> int:
    return len(open_reader(src, password).pages)


def write(writer, out: Path) -> Path:
    """Writes to a .part file first: a full disk or a cancel never leaves half a PDF behind."""
    tmp = out.with_name(out.name + ".part")
    try:
        with open(tmp, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def _keep_details(writer, reader):
    """The title and author of the original stay on the new file."""
    try:
        meta = reader.metadata
        if meta:
            writer.add_metadata({k: v for k, v in meta.items() if isinstance(k, str) and isinstance(v, str)})
    except Exception:
        log.debug("couldn't copy the PDF's details", exc_info=True)


def _pages_writer(readers: list, pages: list[tuple[int, int, int]], ctx: Ctx | None = None, span=(0.0, 0.9)):
    """A new PDF of these pages: (which file, which page in it, extra turn in degrees)."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    n = max(1, len(pages))
    for k, (fi, pi, turn) in enumerate(pages):
        if ctx is not None:
            ctx.check()
        if not 0 <= fi < len(readers) or not 0 <= pi < len(readers[fi].pages):
            raise RuntimeError(tr("the PDF changed since its pages were shown. Open it again"))
        page = writer.add_page(readers[fi].pages[pi])
        if turn % 360:
            page.rotate(turn % 360)
        if ctx is not None:
            ctx.progress(span[0] + (span[1] - span[0]) * (k + 1) / n)
    if readers:
        _keep_details(writer, readers[0])
    return writer


# ----------------------------------------------------------------------------- the page workspace

def save_arranged(files: list[Path], ctx: Ctx) -> Result:
    """Saves the pages as they were arranged on Corefold PDF's page: all of them as one PDF (that's
    merging when they come from several files), each one as its own PDF, or as pictures.
    ctx.params: pages [(file, page, turn)], passwords {path: password}, how ("one" | "each" | "images"),
    name (for the new file)."""
    passwords = ctx.params.get("passwords") or {}
    readers = [open_reader(f, passwords.get(str(f), "")) for f in files]
    pages = [(int(a), int(b), int(c)) for a, b, c in ctx.params.get("pages", [])]
    if not pages:
        raise RuntimeError(tr("there are no pages left"))
    how = ctx.params.get("how", "one")
    first = files[pages[0][0]]
    name = safe_name(ctx.params.get("name", "")) or ctx.stem(first)
    if how == "one":
        out = ctx.out_path(first, name + ".pdf")
        write(_pages_writer(readers, pages, ctx), out)
        return Result([out])
    if how == "each":
        outdir = ctx.out_dir(first, f"{name} pages")
        width = len(str(len(pages)))
        for k, page in enumerate(pages):
            ctx.check()
            write(_pages_writer(readers, [page]), outdir / f"{name} {k + 1:0{width}}.pdf")
            ctx.progress((k + 1) / len(pages))
        return Result([outdir])
    if how == "images":
        return _render(files, pages, passwords, ctx, name, ctx.params.get("format", "png"),
                       int(ctx.params.get("dpi", 150)))
    raise RuntimeError(f"unknown way to save: {how}")


ARRANGED = Tool("pdftool:arranged", tr("Save"), tr("Save the pages as a new PDF"), save_arranged, batch=True,
                renames=False)


# ----------------------------------------------------------------------------- split and take pages out

def split(src: Path, ctx: Ctx) -> Result:
    """mode "each": one PDF per page; "every": a PDF every N pages; "ranges": one PDF per part typed."""
    reader = open_reader(src, ctx.params.get("password", ""))
    n = len(reader.pages)
    mode = ctx.params.get("mode", "each")
    if mode == "every":
        size = max(1, int(ctx.params.get("every", 2)))
        parts = [list(range(i, min(i + size, n))) for i in range(0, n, size)]
    elif mode == "ranges":
        parts = parse_ranges(ctx.params.get("ranges", ""), n)
    else:
        parts = [[i] for i in range(n)]
    if len(parts) == 1 and len(parts[0]) == n and mode != "ranges":
        return Result(note=tr("{name} has only one part: nothing to split", name=src.name))
    stem = ctx.stem(src)
    outdir = ctx.out_dir(src, f"{stem} split")
    width = len(str(n))
    for k, part in enumerate(parts):
        ctx.check()
        label = f"p{part[0] + 1:0{width}}" if len(part) == 1 else \
            f"p{part[0] + 1:0{width}}-{part[-1] + 1:0{width}}"
        write(_pages_writer([reader], [(0, i, 0) for i in part]), outdir / f"{stem} {label}.pdf")
        ctx.progress((k + 1) / len(parts))
    return Result([outdir], note=tr("Split into {count} PDFs", count=len(parts)))


def extract(src: Path, ctx: Ctx) -> Result:
    """The pages typed ("1-3, 7"), in that order, as one new PDF."""
    reader = open_reader(src, ctx.params.get("password", ""))
    pages = [i for part in parse_ranges(ctx.params.get("ranges", ""), len(reader.pages)) for i in part]
    out = ctx.out_path(src, f"{ctx.stem(src)} (pages {ranges_text(pages)}).pdf".replace(", ", ","))
    write(_pages_writer([reader], [(0, i, 0) for i in pages], ctx), out)
    return Result([out])


def remove_pages(src: Path, ctx: Ctx) -> Result:
    """Every page except the ones typed."""
    reader = open_reader(src, ctx.params.get("password", ""))
    n = len(reader.pages)
    gone = {i for part in parse_ranges(ctx.params.get("ranges", ""), n) for i in part}
    keep = [i for i in range(n) if i not in gone]
    if not keep:
        raise RuntimeError(tr("A PDF needs at least one page."))
    out = ctx.out_path(src, f"{ctx.stem(src)} (edited).pdf")
    write(_pages_writer([reader], [(0, i, 0) for i in keep], ctx), out)
    return Result([out], note=tr("Took out {count} pages", count=len(gone)))


# ----------------------------------------------------------------------------- shrink

def compress(src: Path, ctx: Ctx) -> Result:
    from .converters import human_size, write_compressed_pdf
    _, quality, longest = COMPRESS_LEVELS.get(ctx.params.get("level", "normal"), COMPRESS_LEVELS["normal"])
    reader = open_reader(src, ctx.params.get("password", ""))
    out = ctx.out_path(src, f"{ctx.stem(src)} (compressed).pdf")
    write_compressed_pdf(src, out, ctx, quality=quality, max_dim=longest, reader=reader)
    before, after = src.stat().st_size, out.stat().st_size
    if after >= before * 0.97:
        out.unlink(missing_ok=True)
        return Result(note=tr("{name} is already as small as it gets", name=src.name))
    return Result([out], note=tr("{before} → {after}", before=human_size(before), after=human_size(after)))


# ----------------------------------------------------------------------------- pictures to PDF

def _picture_page(im, writer, size, margin: float):
    """One picture as a page: on a page of its own size, or fitted (never stretched) on A4 / Letter,
    turned to landscape when the picture is wider than tall."""
    from pypdf import PdfReader, Transformation
    buf = io.BytesIO()
    im.save(buf, "PDF", resolution=150)
    buf.seek(0)
    pic = PdfReader(buf).pages[0]
    if size is None:
        writer.add_page(pic)
        return
    pw, ph = size
    iw, ih = float(pic.mediabox.width), float(pic.mediabox.height)
    if iw > ih:
        pw, ph = ph, pw
    room_w, room_h = pw - 2 * margin, ph - 2 * margin
    scale = min(room_w / iw, room_h / ih)
    page = writer.add_blank_page(pw, ph)
    x, y = (pw - iw * scale) / 2, (ph - ih * scale) / 2
    page.merge_transformed_page(pic, Transformation().scale(scale).translate(x, y))


def pictures_to_pdf(files: list[Path], ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    from .converters import _all_pages
    _, size = PAGE_SIZES.get(ctx.params.get("size", "picture"), PAGE_SIZES["picture"])
    margin = MARGINS.get(ctx.params.get("margin", "none"), MARGINS["none"])[1] if size else 0
    name = safe_name(ctx.params.get("name", "")) or \
        (files[0].stem if len(files) == 1 else f"{files[0].stem} +{len(files) - 1}")
    out = ctx.out_path(files[0], name + ".pdf")
    writer = PdfWriter()
    for i, f in enumerate(files):
        ctx.check()
        for im in _all_pages(f):  # (a multi-page TIFF gives all its pages)
            _picture_page(im, writer, size, margin)
            im.close()
        ctx.progress((i + 1) / (len(files) + 1))
    write(writer, out)
    return Result([out])


# ----------------------------------------------------------------------------- PDF to pictures

def _render(files: list[Path], pages: list[tuple[int, int, int]], passwords: dict, ctx: Ctx, name: str,
            fmt: str, dpi: int) -> Result:
    import pypdfium2 as pdfium
    docs = {}
    first = files[pages[0][0]]
    ext = ".jpg" if fmt == "jpg" else ".png"
    try:
        if len(pages) == 1:
            targets = [ctx.out_path(first, name + ext)]
            result = targets[0]
        else:
            result = ctx.out_dir(first, f"{name} pictures")
            width = len(str(len(pages)))
            targets = [result / f"{name} {k + 1:0{width}}{ext}" for k in range(len(pages))]
        for k, ((fi, pi, turn), target) in enumerate(zip(pages, targets)):
            ctx.check()
            with PDFIUM:
                if fi not in docs:
                    docs[fi] = pdfium.PdfDocument(str(files[fi]), password=passwords.get(str(files[fi])) or None)
                page = docs[fi][pi]
                try:
                    im = page.render(scale=dpi / 72, rotation=turn % 360).to_pil()
                finally:
                    page.close()
            if fmt == "jpg":
                im.convert("RGB").save(target, quality=90, dpi=(dpi, dpi))
            else:
                im.save(target, dpi=(dpi, dpi))
            im.close()
            ctx.progress((k + 1) / len(pages))
    finally:
        with PDFIUM:
            for doc in docs.values():
                doc.close()
    return Result([result])


def to_pictures(src: Path, ctx: Ctx) -> Result:
    password = ctx.params.get("password", "")
    n = page_count(src, password)
    text = (ctx.params.get("ranges") or "").strip()
    chosen = [i for part in parse_ranges(text, n) for i in part] if text else list(range(n))
    return _render([src], [(0, i, 0) for i in chosen], {str(src): password}, ctx, ctx.stem(src),
                   ctx.params.get("format", "png"), int(ctx.params.get("dpi", 150)))


# ----------------------------------------------------------------------------- passwords

def lock(src: Path, ctx: Ctx) -> Result:
    """AES-256. "Allow printing / copying" are kept as the PDF's own permissions (honoured by PDF apps)."""
    from pypdf import PdfWriter
    from pypdf.constants import UserAccessPermissions as P
    password = ctx.params.get("new_password", "")
    if not password:
        raise RuntimeError(tr("type a password first"))
    writer = PdfWriter(clone_from=open_reader(src, ctx.params.get("password", "")))
    allowed = P.ADD_OR_MODIFY | P.FILL_FORM_FIELDS | P.EXTRACT_TEXT_AND_GRAPHICS | P.ASSEMBLE_DOC
    if ctx.params.get("print", True):
        allowed |= P.PRINT | P.PRINT_TO_REPRESENTATION
    if ctx.params.get("copy", True):
        allowed |= P.EXTRACT
    owner = password if ctx.params.get("print", True) and ctx.params.get("copy", True) else os.urandom(16).hex()
    writer.encrypt(user_password=password, owner_password=owner, algorithm="AES-256", permissions_flag=allowed)
    out = ctx.out_path(src, f"{ctx.stem(src)} (locked).pdf")
    write(writer, out)
    return Result([out], note=tr("Protected with your password"))


def unlock(src: Path, ctx: Ctx) -> Result:
    from pypdf import PdfWriter
    writer = PdfWriter(clone_from=open_reader(src, ctx.params.get("password", "")))
    out = ctx.out_path(src, f"{ctx.stem(src)} (unlocked).pdf")
    write(writer, out)
    return Result([out], note=tr("The password is gone from the new copy"))


def lock_state(src: Path) -> str:
    """"open", "restricted" (opens, but has limits) or "locked"."""
    from .converters import pdf_lock_state
    return pdf_lock_state(src)


# the tools on Corefold PDF's page (the Converter's wheel keeps its own, simpler ones)
SPLIT = Tool("pdftool:split", tr("Split"), tr("Split a PDF"), split, password=True, renames=False)
EXTRACT = Tool("pdftool:extract", tr("Take out pages"), tr("Save some pages as a new PDF"), extract, password=True,
               renames=False)
REMOVE = Tool("pdftool:remove", tr("Delete pages"), tr("Delete pages"), remove_pages, password=True, renames=False)
COMPRESS = Tool("pdftool:compress", tr("Compress"), tr("Make the file smaller"), compress, password=True,
                renames=False)
PICTURES = Tool("pdftool:pictures", tr("Pictures to PDF"), tr("Make a PDF from pictures"), pictures_to_pdf,
                batch=True, renames=False)
TO_PICTURES = Tool("pdftool:topictures", tr("PDF to pictures"), tr("Save pages as pictures"), to_pictures,
                   password=True, renames=False)
LOCK = Tool("pdftool:lock", tr("Lock"), tr("Protect with a password"), lock, password=True, renames=False)
UNLOCK = Tool("pdftool:unlock", tr("Unlock"), tr("Remove the password"), unlock, password=True, renames=False)
