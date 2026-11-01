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

from .core import Ctx, Result, Tool, render_scale, safe_name
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
# "Page size": the size every page is made (width, height in points, portrait; a page that's wider than it is
# tall is made landscape)
PAGE_FORMATS = {"a4": ("A4", (595.28, 841.89)), "letter": ("Letter", (612.0, 792.0)),
                "a3": ("A3", (841.89, 1190.55)), "a5": ("A5", (419.53, 595.28)), "legal": ("Legal", (612.0, 1008.0))}
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


def open_pdfium(src: Path, password: str = ""):
    """The PDF in PDFium, ready to draw what's typed in its form too (PDFium leaves a form's boxes out of
    its pictures unless it's told about the form first). Call with PDFIUM held."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(src), password=password or None)
    try:
        doc.init_forms()
    except Exception:
        log.debug("the form of %s couldn't be read", src, exc_info=True)
    return doc


def page_count(src: Path, password: str = "") -> int:
    return len(open_reader(src, password).pages)


def write(writer, out: Path, password: str = "") -> Path:
    """Writes to a .part file first: a full disk or a cancel never leaves half a PDF behind. With a password
    (the original had one), the new copy is locked with it too."""
    if password:
        writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
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
    from pypdf.generic import ArrayObject, NameObject, NumberObject
    for fi, pi, _ in pages:
        if not 0 <= fi < len(readers) or not 0 <= pi < len(readers[fi].pages):
            raise RuntimeError(tr("the PDF changed since its pages were shown. Open it again"))
    writer = PdfWriter()
    if len(set((fi, pi) for fi, pi, _ in pages)) < len(pages):  # (the same page twice: one at a time)
        for fi, pi, turn in pages:
            page = writer.add_page(readers[fi].pages[pi])
            if turn % 360:
                page.rotate(turn % 360)
    else:
        # each PDF's pages go in together (so its form, its bookmarks and the links between its pages come
        # along), then they're put in the order asked for
        at: dict[tuple[int, int], int] = {}
        files = list(dict.fromkeys(fi for fi, _, _ in pages))
        for k, fi in enumerate(files):
            if ctx is not None:
                ctx.check()
            wanted = [pi for f, pi, _ in pages if f == fi]
            first = len(writer.pages)
            writer.append(readers[fi], pages=wanted)
            at.update({(fi, pi): first + j for j, pi in enumerate(wanted)})
            if ctx is not None:
                ctx.progress(span[0] + (span[1] - span[0]) * (k + 1) / len(files))
        added = list(writer.pages)
        ordered = []
        for fi, pi, turn in pages:
            page = added[at[(fi, pi)]]
            if turn % 360:
                page.rotate(turn % 360)
            ordered.append(page.indirect_reference)
        root = writer._root_object["/Pages"]
        root[NameObject("/Kids")] = ArrayObject(ordered)
        root[NameObject("/Count")] = NumberObject(len(ordered))
        writer.flattened_pages = None  # (pypdf's own list of the pages: made again when it's needed)
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
    # (a locked PDF's own pages stay locked with its password; merged with other files they come out
    # open, as before)
    used = {fi for fi, _, _ in pages}
    password = passwords.get(str(files[next(iter(used))]), "") if len(used) == 1 else ""
    if how == "one":
        out = ctx.out_path(first, name + ".pdf")
        write(_pages_writer(readers, pages, ctx), out, password)
        return Result([out])
    if how == "each":
        outdir = ctx.out_dir(first, f"{name} pages")
        width = len(str(len(pages)))
        for k, page in enumerate(pages):
            ctx.check()
            write(_pages_writer(readers, [page]), outdir / f"{name} {k + 1:0{width}}.pdf", password)
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
    used: set[str] = set()
    for k, part in enumerate(parts):
        ctx.check()
        label = f"p{part[0] + 1:0{width}}" if len(part) == 1 else \
            f"p{part[0] + 1:0{width}}-{part[-1] + 1:0{width}}"
        # the same part typed twice ("2, 2") gets its own file instead of replacing the first one
        base, copy = label, 2
        while label.casefold() in used:
            label, copy = f"{base} ({copy})", copy + 1
        used.add(label.casefold())
        write(_pages_writer([reader], [(0, i, 0) for i in part]), outdir / f"{stem} {label}.pdf",
              ctx.params.get("password", ""))
        ctx.progress((k + 1) / len(parts))
    return Result([outdir], note=tr("Split into {count} PDFs", count=len(parts)))


def extract(src: Path, ctx: Ctx) -> Result:
    """The pages typed ("1-3, 7"), in that order, as one new PDF."""
    reader = open_reader(src, ctx.params.get("password", ""))
    pages = [i for part in parse_ranges(ctx.params.get("ranges", ""), len(reader.pages)) for i in part]
    out = ctx.out_path(src, f"{ctx.stem(src)} (pages {ranges_text(pages)}).pdf".replace(", ", ","))
    write(_pages_writer([reader], [(0, i, 0) for i in pages], ctx), out, ctx.params.get("password", ""))
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
    write(_pages_writer([reader], [(0, i, 0) for i in keep], ctx), out, ctx.params.get("password", ""))
    return Result([out], note=tr("Took out {count} pages", count=len(gone)))


# ----------------------------------------------------------------------------- shrink

def compress(src: Path, ctx: Ctx) -> Result:
    from .converters import human_size, write_compressed_pdf
    _, quality, longest = COMPRESS_LEVELS.get(ctx.params.get("level", "normal"), COMPRESS_LEVELS["normal"])
    reader = open_reader(src, ctx.params.get("password", ""))
    out = ctx.out_path(src, f"{ctx.stem(src)} (compressed).pdf")
    # (a locked PDF's smaller copy is locked too)
    write_compressed_pdf(src, out, ctx, quality=quality, max_dim=longest, reader=reader,
                         password=ctx.params.get("password", ""))
    before, after = src.stat().st_size, out.stat().st_size
    if after >= before * 0.97:
        out.unlink(missing_ok=True)
        return Result(note=tr("{name} is already as small as it gets", name=src.name))
    return Result([out], note=tr("{before} → {after}", before=human_size(before), after=human_size(after)))


# ----------------------------------------------------------------------------- pictures to PDF

# what a JPG carries besides the picture that's kept in a PDF: JFIF (its sharpness), its colour profile and
# Adobe's colour note (without them the colours come out wrong). The rest (the camera's details: where it
# was taken, the phone, the date, a second picture) never goes into a PDF.
_JPEG_KEEP = {0xE0, 0xEE}


def jpeg_without_details(data: bytes) -> bytes:
    """The JPG without its camera details (EXIF with the GPS place, XMP, IPTC, notes): the picture's own
    bytes aren't touched. Anything that doesn't look like a JPG comes back as it was."""
    if data[:2] != b"\xff\xd8":
        return data
    out, i, n = [b"\xff\xd8"], 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return data
        marker = data[i + 1]
        if marker == 0xFF:  # (fill bytes before a marker)
            i += 1
            continue
        if marker == 0xDA or not (0xC0 <= marker <= 0xFE):  # the picture itself starts: all the rest is kept
            out.append(data[i:])
            return b"".join(out)
        size = int.from_bytes(data[i + 2:i + 4], "big")
        if size < 2 or i + 2 + size > n:
            return data
        part = data[i:i + 2 + size]
        icc = marker == 0xE2 and part[4:16] == b"ICC_PROFILE\0"
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE) or marker in _JPEG_KEEP or icc:
            out.append(part)
        i += 2 + size
    return data


def _strip_photo_details(writer) -> int:
    """The JPG pictures inside the PDF lose their camera details (see jpeg_without_details). Gives how many."""
    from pypdf.generic import StreamObject
    count = 0
    for obj in writer._objects:
        try:
            kind = obj.get("/Filter") if isinstance(obj, StreamObject) else None
            if isinstance(kind, list) and len(kind) == 1:
                kind = kind[0]
            if obj.get("/Subtype") != "/Image" or kind != "/DCTDecode":
                continue
            data = obj._data
            plain = jpeg_without_details(data)
            if len(plain) < len(data):
                obj._data = plain
                count += 1
        except Exception:
            log.debug("kept one picture's details", exc_info=True)
    return count


def _jpeg_source(f: Path) -> tuple[bool, bytes | None, bool]:
    """(is it a JPG photo, its own bytes when they can go into the PDF as they are, is it grey)"""
    from PIL import Image
    try:
        with Image.open(f) as im:
            if im.format != "JPEG":
                return False, None, False
            plain = im.mode in ("RGB", "L") and (im.getexif().get(0x0112, 1) or 1) == 1
            return True, (jpeg_without_details(f.read_bytes()) if plain else None), im.mode == "L"
    except Exception:
        return False, None, False


def _picture_page(im, writer, size, margin: float, jpeg: bytes | None = None, photo: bool = False,
                  grey: bool = False):
    """One picture as a page: on a page of its own size, or fitted (never stretched) on A4 / Letter,
    turned to landscape when the picture is wider than tall. A JPG photo goes in as it is (turned by the
    phone: once more, at high quality); other pictures are kept exactly, never made into a JPG."""
    import zlib
    from pypdf.generic import ContentStream, DictionaryObject, NameObject, NumberObject, StreamObject
    pic = StreamObject()
    if jpeg is None and photo:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=95)
        jpeg, grey = buf.getvalue(), False
    if jpeg is not None:
        pic._data, space, kind = jpeg, "/DeviceGray" if grey else "/DeviceRGB", "/DCTDecode"
    else:
        im = im if im.mode in ("RGB", "L") else im.convert("RGB")
        pic._data, space, kind = zlib.compress(im.tobytes(), 6), "/DeviceGray" if im.mode == "L" else "/DeviceRGB", \
            "/FlateDecode"
    pic.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(im.width), NameObject("/Height"): NumberObject(im.height),
                NameObject("/ColorSpace"): NameObject(space), NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/Filter"): NameObject(kind)})
    iw, ih = im.width * 72 / 150, im.height * 72 / 150  # (150 dpi, as before)
    if size is None:
        pw, ph, scale, margin = iw, ih, 1.0, 0
    else:
        pw, ph = size
        if iw > ih:
            pw, ph = ph, pw
        scale = min((pw - 2 * margin) / iw, (ph - 2 * margin) / ih)
    page = writer.add_blank_page(pw, ph)
    x, y = (pw - iw * scale) / 2, (ph - ih * scale) / 2
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject(
        {NameObject("/Im0"): writer._add_object(pic)})})
    stream = ContentStream(None, writer)
    stream.set_data(f"q {iw * scale:.4f} 0 0 {ih * scale:.4f} {x:.4f} {y:.4f} cm /Im0 Do Q".encode("ascii"))
    page.replace_contents(stream)


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
        photo, jpeg, grey = _jpeg_source(f)
        for im in _all_pages(f):  # (a multi-page TIFF gives all its pages)
            _picture_page(im, writer, size, margin, jpeg, photo, grey)
            im.close()
        ctx.progress((i + 1) / (len(files) + 1))
    write(writer, out)
    return Result([out])


# ----------------------------------------------------------------------------- PDF to pictures

def _render(files: list[Path], pages: list[tuple[int, int, int]], passwords: dict, ctx: Ctx, name: str,
            fmt: str, dpi: int) -> Result:
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
                    docs[fi] = open_pdfium(files[fi], passwords.get(str(files[fi])) or "")
                page = docs[fi][pi]
                try:
                    im = page.render(scale=render_scale(page.get_width(), page.get_height(), dpi / 72),
                                     rotation=turn % 360).to_pil()
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


# ----------------------------------------------------------------------------- hidden information

# what "Remove hidden info" can take out, in the order it's shown: (key, what it is, the line under it)
HIDDEN = [("details", "Who made it and with what",
           "The author, the program, the dates, the keywords and where photos were taken"),
          ("notes", "Notes", "Sticky notes and replies, which only show when someone opens them"),
          ("attachments", "Attached files", "Files carried inside the PDF"),
          ("scripts", "Scripts and automatic actions", "Code that runs or opens things by itself when the PDF "
                                                       "or a page opens"),
          ("private", "Programs' private data", "What the program that made it kept for itself, and page "
                                                "previews")]
_NOTES = {"/Text", "/Popup", "/Sound"}
_RISKY_ACTIONS = {"/JavaScript", "/Launch", "/ImportData", "/SubmitForm", "/ResetForm", "/Rendition"}
_OPENS_FILES = {"/GoToR", "/GoToE", "/URI", "/Thread", "/Sound", "/Movie", "/Hide", "/Named", "/SetOCGState",
                "/Trans", "/GoTo3DView", "/RichMediaExecute"}
_DETAIL_NAMES = ("/Title", "/Author", "/Subject", "/Keywords", "/Creator", "/Producer", "/CreationDate", "/ModDate")


def _text(v) -> str:
    try:
        return str(v.get_object()).strip() if v is not None else ""
    except Exception:
        return ""


def _risky(action, risky=_RISKY_ACTIONS) -> bool:
    """An action that runs code, starts a program or sends something away (a web link is fine). Its chain of
    next actions is followed once (a booby-trapped PDF whose action leads back to itself kept this busy for
    ever)."""
    try:
        todo, seen = [action], set()
        while todo and len(seen) < 1000:
            ref = todo.pop()
            key = getattr(ref, "idnum", None) or id(ref)
            if key in seen:
                continue
            seen.add(key)
            action = ref.get_object()
            if isinstance(action, list):  # (several actions one after the other)
                todo.extend(action)
                continue
            if action is None or not hasattr(action, "get"):
                continue
            if action.get("/S") in risky:
                return True
            nxt = action.get("/Next")
            if nxt is not None:
                todo.append(nxt)
        return bool(todo)  # (a chain too long to follow is taken out too)
    except Exception:
        return True  # (one that can't be read is taken out too)


def _risky_by_itself(action) -> bool:
    """An action that happens with nobody clicking (when the PDF opens): anything but going to one of its
    own pages, so it can't open another file or a share on the network either (which can give away who you
    are on Windows)."""
    return _risky(action, _RISKY_ACTIONS | _OPENS_FILES)


def _outlines(root):
    """Every bookmark (and the ones under it)."""
    try:
        first = root["/Outlines"].get_object().get("/First") if "/Outlines" in root else None
    except Exception:
        return
    todo, seen = [first] if first is not None else [], set()
    while todo and len(seen) < 100_000:
        ref = todo.pop()
        key = getattr(ref, "idnum", None) or id(ref)
        if key in seen:
            continue
        seen.add(key)
        try:
            item = ref.get_object()
        except Exception:
            continue
        if not hasattr(item, "get"):
            continue
        yield item
        todo.extend(x for x in (item.get("/Next"), item.get("/First")) if x is not None)


def _xfa_scripts(root) -> bool:
    """An XFA form (Adobe's other kind of form) with scripts in it."""
    try:
        xfa = root["/AcroForm"].get_object().get("/XFA") if "/AcroForm" in root else None
        if xfa is None:
            return False
        xfa = xfa.get_object()
        parts = [xfa.get_data()] if hasattr(xfa, "get_data") else \
            [x.get_object().get_data() for x in list(xfa)[1::2] if hasattr(x.get_object(), "get_data")]
        return any(b"<script" in part.lower() for part in parts)
    except Exception:
        return True  # (one that can't be read is taken out too)


def _annots(page) -> list:
    try:
        return [a.get_object() for a in (page.get("/Annots") or [])]
    except Exception:
        return []


def _fields(root):
    """Every form field, kids too (their own scripts work out sums and checks as you type)."""
    seen, todo = set(), list((root.get("/AcroForm") or {}).get("/Fields") or []) if root.get("/AcroForm") else []
    while todo:
        ref = todo.pop()
        key = getattr(ref, "idnum", None) or id(ref)
        if key in seen:
            continue
        seen.add(key)
        field = ref.get_object()
        yield field
        todo.extend(field.get("/Kids") or [])


def _names(root) -> dict:
    try:
        return root["/Names"].get_object() if "/Names" in root else {}
    except Exception:
        return {}


def _name_tree_count(tree) -> int:
    """How many entries a name tree has (attached files, scripts)."""
    count, todo, seen = 0, [tree], 0
    while todo and seen < 10_000:
        seen += 1
        node = todo.pop().get_object()
        count += len(node.get("/Names") or []) // 2
        todo.extend(node.get("/Kids") or [])
    return count


def hidden_info(src: Path, password: str = "") -> dict:
    """What a PDF carries that you can't see on its pages: {"title", "author", "details": [(name, value)],
    "notes", "attachments", "scripts", "private"} (the last four are counts)."""
    reader = open_reader(src, password)
    root = reader.trailer["/Root"].get_object()
    info = reader.trailer.get("/Info")
    info = info.get_object() if info is not None else {}
    details = [(k[1:], _text(info.get(k))) for k in _DETAIL_NAMES if _text(info.get(k))]
    if "/Metadata" in root:
        details.append(("XMP", tr("more details, written as XML")))
    names = _names(root)
    found = {"title": _text(info.get("/Title")), "author": _text(info.get("/Author")), "details": details,
             "notes": 0, "attachments": 0, "scripts": 0, "private": 0}
    if "/EmbeddedFiles" in names:
        found["attachments"] += _name_tree_count(names["/EmbeddedFiles"])
    if "/JavaScript" in names:
        found["scripts"] += _name_tree_count(names["/JavaScript"])
    if "/AA" in root or ("/OpenAction" in root and _is_action(root["/OpenAction"]) and
                         _risky_by_itself(root["/OpenAction"])):
        found["scripts"] += 1
    found["scripts"] += _xfa_scripts(root)
    found["scripts"] += sum("/A" in b and _risky(b["/A"]) for b in _outlines(root))
    found["private"] += "/PieceInfo" in root
    for page in reader.pages:
        found["scripts"] += "/AA" in page
        found["private"] += ("/PieceInfo" in page) + ("/Thumb" in page) + ("/Metadata" in page)
        for a in _annots(page):
            kind = a.get("/Subtype")
            if kind in _NOTES:
                found["notes"] += kind != "/Popup"  # (a note's pop-up is part of the note)
            elif kind == "/FileAttachment":
                found["attachments"] += 1
            found["scripts"] += ("/AA" in a) + ("/A" in a and _risky(a["/A"]))
    found["scripts"] += sum("/AA" in f for f in _fields(root))
    return found


def _is_action(v) -> bool:
    try:
        return "/S" in v.get_object()
    except Exception:
        return False


def _drop_unreachable(writer):
    """Leaves out every object nothing leads to any more from the PDF's start, so what was taken out isn't
    still in the file, only unlinked (pypdf keeps whatever it copied)."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
    reach, todo = set(), [writer._root_object]
    if writer._info is not None:
        todo.append(writer._info)
    for obj in todo:
        ref = getattr(obj, "indirect_reference", None)
        if ref is not None:
            reach.add(ref.idnum)
    while todo:
        obj = todo.pop()
        if isinstance(obj, IndirectObject):
            if obj.idnum in reach:
                continue
            reach.add(obj.idnum)
            try:
                obj = obj.get_object()
            except Exception:
                continue
        if isinstance(obj, DictionaryObject):
            todo.extend(v for v in obj.values() if isinstance(v, (IndirectObject, DictionaryObject, ArrayObject)))
        elif isinstance(obj, ArrayObject):
            todo.extend(v for v in obj if isinstance(v, (IndirectObject, DictionaryObject, ArrayObject)))
    for i in range(len(writer._objects)):
        if i + 1 not in reach:
            writer._objects[i] = None


def remove_hidden(src: Path, ctx: Ctx) -> Result:
    """A copy without what's picked in ctx.params["remove"] (keys of HIDDEN), with the title and author
    typed (left empty: none). What's on the pages, links and form boxes stay as they are."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, NameObject, create_string_object
    password = ctx.params.get("password", "")
    remove = set(ctx.params.get("remove") or [k for k, *_ in HIDDEN])
    writer = PdfWriter(clone_from=open_reader(src, password))
    root = writer._root_object
    names = _names(root)
    if "details" in remove:
        writer.metadata = None
        root.pop(NameObject("/Metadata"), None)
        _strip_photo_details(writer)  # (photos put in a PDF often still say where they were taken)
    for key in ("title", "author"):
        if key in ctx.params:  # (asked for: what's typed, or nothing)
            value = (ctx.params[key] or "").strip()
            name = NameObject("/" + key.capitalize())
            if writer._info is None:
                writer.metadata = {}
            info = writer._info.get_object()
            if value:
                info[name] = create_string_object(value)
            else:
                info.pop(name, None)
    if "attachments" in remove:
        names.pop(NameObject("/EmbeddedFiles"), None)
        root.pop(NameObject("/AF"), None)
    if "scripts" in remove:
        names.pop(NameObject("/JavaScript"), None)
        root.pop(NameObject("/AA"), None)
        if "/OpenAction" in root and _is_action(root["/OpenAction"]) and _risky_by_itself(root["/OpenAction"]):
            root.pop(NameObject("/OpenAction"), None)
        for field in _fields(root):
            field.pop(NameObject("/AA"), None)
        if _xfa_scripts(root):  # (the form's ordinary boxes stay: every PDF app but Adobe's uses those)
            root["/AcroForm"].get_object().pop(NameObject("/XFA"), None)
        for bookmark in _outlines(root):  # (the bookmark stays, without what it ran)
            if "/A" in bookmark and _risky(bookmark["/A"]):
                bookmark.pop(NameObject("/A"), None)
    if "private" in remove:
        root.pop(NameObject("/PieceInfo"), None)
    gone = set()
    if "notes" in remove:
        gone |= _NOTES
    if "attachments" in remove:
        gone.add("/FileAttachment")
    for i, page in enumerate(writer.pages):
        ctx.check()
        if "scripts" in remove:
            page.pop(NameObject("/AA"), None)
        if "private" in remove:
            for k in ("/PieceInfo", "/Thumb", "/Metadata"):
                page.pop(NameObject(k), None)
        if "/Annots" in page:
            kept = ArrayObject()
            for ref in page["/Annots"]:
                a = ref.get_object()
                if a.get("/Subtype") in gone:
                    continue
                if "scripts" in remove:
                    a.pop(NameObject("/AA"), None)
                    if "/A" in a and _risky(a["/A"]):
                        a.pop(NameObject("/A"), None)
                        if a.get("/Subtype") == "/Link" and "/Dest" not in a:
                            continue  # (a link with nothing left to do)
                kept.append(ref)
            if kept:
                page[NameObject("/Annots")] = kept
            else:
                page.pop(NameObject("/Annots"), None)
        ctx.progress((i + 1) / (len(writer.pages) + 1))
    if not names and "/Names" in root:
        root.pop(NameObject("/Names"), None)
    _drop_unreachable(writer)
    out = ctx.out_path(src, f"{ctx.stem(src)} (cleaned).pdf")
    write(writer, out, password)
    return Result([out], note=tr("Saved as a new file without the hidden information"))


# ----------------------------------------------------------------------------- flatten, repair, page size

def _relocked(tmp: Path, out: Path, password: str, original=None, no_form: bool = False):
    """A PDF PDFium saved (always without its password) written again with pypdf: locked again with the
    original's password, with its title and author, through a .part file."""
    from pypdf import PdfReader, PdfWriter
    try:
        writer = PdfWriter(clone_from=PdfReader(str(tmp)))
        if no_form:  # (its fields are part of the pages now: no form left for a reader to offer to fill in)
            writer._root_object.pop("/AcroForm", None)
        if original is not None:
            _keep_details(writer, original)
        write(writer, out, password)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def flatten(src: Path, ctx: Ctx) -> Result:
    """What's filled in on a form, and notes, stamps, highlights and drawings, made part of the page: it
    looks the same everywhere and can't be changed or taken off any more (before sending a filled-in form)."""
    import pypdfium2.raw as raw
    password = ctx.params.get("password", "")
    reader = open_reader(src, password)
    out = ctx.out_path(src, f"{ctx.stem(src)} (flattened).pdf")
    tmp = out.with_name(out.name + ".pdfium.part")
    done = 0
    with PDFIUM:
        doc = open_pdfium(src, password)
        try:
            n = len(doc)
            for i in range(n):
                ctx.check()
                page = doc[i]
                try:
                    # (pypdfium2 wants a form for its flatten; a PDF without one can still have notes and stamps)
                    if raw.FPDFPage_Flatten(page.raw, raw.FLAT_NORMALDISPLAY) == raw.FLATTEN_SUCCESS:
                        done += 1
                finally:
                    page.close()
                ctx.progress(0.8 * (i + 1) / n)
            if not done:
                return Result(note=tr("{name} has nothing to flatten: no form, notes or stamps", name=src.name))
            doc.save(str(tmp), flags=raw.FPDF_REMOVE_SECURITY)
        finally:
            doc.close()
    _relocked(tmp, out, password, reader, no_form=True)
    return Result([out], note=tr("Flattened {count} pages", count=done))


def repair(src: Path, ctx: Ctx) -> Result:
    """A damaged PDF (cut off when it was downloaded, or saved wrong by a program) read the forgiving way and
    written again whole, with a new table of where everything is."""
    from pypdf import PdfReader, PdfWriter
    password = ctx.params.get("password", "")
    out = ctx.out_path(src, f"{ctx.stem(src)} (repaired).pdf")
    tmp = out.with_name(out.name + ".pdfium.part")
    pages = 0
    try:
        import pypdfium2.raw as raw
        with PDFIUM:
            doc = open_pdfium(src, password)
            try:
                pages = len(doc)
                doc.save(str(tmp), flags=raw.FPDF_REMOVE_SECURITY)
            finally:
                doc.close()
        ctx.progress(0.6)
        _relocked(tmp, out, password)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        if type(e).__name__ == "NeedPassword":
            raise
        log.debug("PDFium couldn't repair %s, trying pypdf", src, exc_info=True)
        try:
            reader = PdfReader(str(src), strict=False)
        except Exception:
            raise RuntimeError(tr("nothing could be saved from {name}: it's too damaged", name=src.name)) from e
        if reader.is_encrypted and not reader.decrypt(password or ""):
            from .archives import NeedPassword
            raise NeedPassword() from None
        writer = PdfWriter()
        for page in reader.pages:
            ctx.check()
            try:
                writer.add_page(page)
            except Exception:
                log.debug("a page of %s couldn't be saved", src, exc_info=True)
        pages = len(writer.pages)
        if not pages:
            raise RuntimeError(tr("nothing could be saved from {name}: it's too damaged", name=src.name)) from e
        _keep_details(writer, reader)
        write(writer, out, password)
    return Result([out], note=tr("{count} pages saved in a repaired copy", count=pages))


def _turn_looks(annot, angle: int, done: set):
    """A note's ready-made looks (its /AP streams) turned the way its page was shown turned, so it looks the
    same once the page itself is turned for real (each look once, even when notes share one)."""
    from pypdf import Transformation
    from pypdf.generic import ArrayObject, FloatObject, NameObject, StreamObject
    looks = annot.get("/AP")
    if looks is None:
        return
    looks = looks.get_object()
    for kind in ("/N", "/D", "/R"):
        if kind not in looks:
            continue
        one = looks[kind].get_object()
        streams = [one] if isinstance(one, StreamObject) else [v.get_object() for v in one.values()]
        for stream in streams:
            if not isinstance(stream, StreamObject) or id(stream) in done:
                continue
            done.add(id(stream))
            old = [float(v) for v in stream.get("/Matrix", [1, 0, 0, 1, 0, 0])]
            ctm = Transformation(tuple(old)).rotate(-angle).ctm
            stream[NameObject("/Matrix")] = ArrayObject(FloatObject(round(v, 6)) for v in ctm)


def page_size(src: Path, ctx: Ctx) -> Result:
    """Every page made the same size (A4, Letter…): what's on it made bigger or smaller to fit inside, in the
    middle, keeping its shape. A page wider than it is tall becomes a landscape page. Links and notes go
    along with what they're on."""
    from pypdf import PdfWriter, Transformation
    from pypdf.generic import ArrayObject, FloatObject, NameObject, RectangleObject, StreamObject
    password = ctx.params.get("password", "")
    key = ctx.params.get("size", "a4")
    name, (tw, th) = PAGE_FORMATS.get(key, PAGE_FORMATS["a4"])
    margin = float(MARGINS.get(ctx.params.get("margin", "none"), MARGINS["none"])[1])
    reader = open_reader(src, password)
    writer = PdfWriter(clone_from=reader)
    n = len(writer.pages)
    for i, page in enumerate(writer.pages):
        ctx.check()
        angle, turn = page.rotation % 360, None
        if angle:  # (a turned page is turned for real: what's on it, and its links and notes with it)
            mb = RectangleObject(page.mediabox)
            turn = Transformation().translate(-float(mb.left + mb.width / 2),
                                              -float(mb.bottom + mb.height / 2)).rotate(-angle)
            ends = [turn.apply_on(p) for p in (mb.lower_left, mb.upper_right)]
            turn = turn.translate(-min(e[0] for e in ends), -min(e[1] for e in ends))
        box = page.cropbox
        ox0, oy0, ow, oh = float(box.left), float(box.bottom), float(box.width), float(box.height)
        if ow <= 0 or oh <= 0:
            continue
        ends = [(ox0, oy0), (ox0 + ow, oy0 + oh)]
        if turn is not None:
            ends = [turn.apply_on(e) for e in ends]
        x0, y0 = min(e[0] for e in ends), min(e[1] for e in ends)
        w, h = abs(ends[1][0] - ends[0][0]), abs(ends[1][1] - ends[0][1])
        W, H = (th, tw) if w > h else (tw, th)
        scale = min((W - 2 * margin) / w, (H - 2 * margin) / h)
        dx, dy = (W - w * scale) / 2 - x0 * scale, (H - h * scale) / 2 - y0 * scale
        # (what was outside the page's visible box stays hidden, such as a print PDF's crop marks and bleed, or
        # the edges Trim edges hid: everything on the page is drawn through that box)
        contents = page.get_contents()
        clipped = StreamObject()
        clipped.set_data(f"q {ox0:.3f} {oy0:.3f} {ow:.3f} {oh:.3f} re W n\n".encode()
                         + (contents.get_data() if contents is not None else b"") + b"\nQ")
        page.replace_contents(clipped.flate_encode())
        whole = (Transformation(turn.ctm) if turn is not None else Transformation()).scale(scale, scale) \
            .translate(dx, dy)
        page.add_transformation(whole)
        if angle:
            page.rotation = 0
        for k in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
            page.pop(NameObject(k), None)
        page.mediabox = RectangleObject([0, 0, W, H])

        def moved(values, rect=False, whole=whole):
            spots = [whole.apply_on((float(values[j]), float(values[j + 1]))) for j in range(0, len(values) - 1, 2)]
            if rect:  # (a box: its corners again lowest first, as a turn can swap them)
                xs, ys = [s[0] for s in spots], [s[1] for s in spots]
                spots = [(min(xs), min(ys)), (max(xs), max(ys))]
            return ArrayObject(FloatObject(round(v, 3)) for spot in spots for v in spot)
        turned_looks: set = set()
        for ref in page.get("/Annots") or []:
            try:
                annot = ref.get_object()
                for k in ("/Rect", "/QuadPoints", "/Vertices", "/L"):
                    if k in annot:
                        annot[NameObject(k)] = moved(annot[k], rect=k == "/Rect")
                if "/InkList" in annot:
                    annot[NameObject("/InkList")] = ArrayObject(moved(stroke) for stroke in annot["/InkList"])
                if angle and not int(annot.get("/F", 0)) & 16:  # (how a note looks turns with the page too)
                    _turn_looks(annot, angle, turned_looks)
            except Exception:
                log.debug("a note on page %d couldn't be moved", i + 1, exc_info=True)
        ctx.progress((i + 1) / n)
    out = ctx.out_path(src, f"{ctx.stem(src)} ({name}).pdf")
    write(writer, out, password)
    return Result([out], note=tr("Every page is now {size}", size=tr(name)))


def _ink_box(page, raw, ctypes):
    """Where something is drawn on a PDFium page (x0, y0, x1, y1 in the PDF's own points), or None for an
    empty page. Specks of a scan (a dot or two on their own) don't count."""
    from PIL import ImageFilter
    w, h = page.get_size()
    scale = min(1.0, 1800 / max(w, h, 1))
    img = page.render(scale=scale, grayscale=True, may_draw_forms=True).to_pil().convert("L")
    ink = img.point(lambda v: 255 if v < 235 else 0).filter(ImageFilter.MedianFilter(3))
    box = ink.getbbox()
    if not box:
        return None
    px0, py0, px1, py1 = box
    xs, ys = [], []
    for dx, dy in ((px0, py0), (px1, py0), (px0, py1), (px1, py1)):
        x, y = ctypes.c_double(), ctypes.c_double()
        raw.FPDF_DeviceToPage(page.raw, 0, 0, img.width, img.height, 0, dx, dy, ctypes.byref(x), ctypes.byref(y))
        xs.append(x.value)
        ys.append(y.value)
    return min(xs), min(ys), max(xs), max(ys)


def trim(src: Path, ctx: Ctx) -> Result:
    """The empty white edges around what's on every page cut away (a scan's wide borders, a slide's empty
    room), with a little room left. "same": every page of a size cut the same, so they all still match.
    What's cut away is only hidden (the PDF's own way of showing part of a page), not taken out."""
    import ctypes

    import pypdfium2.raw as raw
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject
    password = ctx.params.get("password", "")
    room = float(MARGINS.get(ctx.params.get("margin", "small"), MARGINS["small"])[1])
    same = bool(ctx.params.get("same", True))
    reader = open_reader(src, password)
    n = len(reader.pages)
    boxes: list = []
    with PDFIUM:
        doc = open_pdfium(src, password)
        try:
            for i in range(n):
                ctx.check()
                page = doc[i]
                try:
                    boxes.append(_ink_box(page, raw, ctypes))
                finally:
                    page.close()
                ctx.progress(0.9 * (i + 1) / n)
        finally:
            doc.close()
    writer = PdfWriter(clone_from=reader)
    if same:  # (pages of the same size and turn: one box around what's on all of them)
        groups: dict = {}
        for i, page in enumerate(writer.pages):
            if boxes[i]:
                c = page.cropbox
                key = (round(float(c.left)), round(float(c.bottom)), round(float(c.width)),
                       round(float(c.height)), page.rotation % 360)
                g = groups.setdefault(key, list(boxes[i]))
                g[:] = [min(g[0], boxes[i][0]), min(g[1], boxes[i][1]), max(g[2], boxes[i][2]),
                        max(g[3], boxes[i][3])]
                boxes[i] = g
    done = 0
    for i, page in enumerate(writer.pages):
        if not boxes[i]:
            continue
        c = page.cropbox
        cx0, cy0, cx1, cy1 = float(c.left), float(c.bottom), float(c.right), float(c.top)
        x0, y0, x1, y1 = (max(cx0, boxes[i][0] - room), max(cy0, boxes[i][1] - room),
                          min(cx1, boxes[i][2] + room), min(cy1, boxes[i][3] + room))
        if x1 - x0 < 8 or y1 - y0 < 8 or (x1 - x0) * (y1 - y0) > 0.98 * (cx1 - cx0) * (cy1 - cy0):
            continue  # (nothing worth cutting)
        page.cropbox = RectangleObject([round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)])
        done += 1
    if not done:
        return Result([], note=tr("{name} has no empty edges to trim", name=src.name))
    out = ctx.out_path(src, f"{ctx.stem(src)} (trimmed).pdf")
    write(writer, out, password)
    ctx.progress(1.0)
    return Result([out], note=tr("Trimmed the edges of {count} pages", count=done))


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
CLEAN = Tool("pdftool:clean", tr("Remove hidden info"), tr("Remove hidden information"), remove_hidden,
             password=True, renames=False)
UNLOCK = Tool("pdftool:unlock", tr("Unlock"), tr("Remove the password"), unlock, password=True, renames=False)
FLATTEN = Tool("pdftool:flatten", tr("Flatten"), tr("Make forms and notes part of the page"), flatten, password=True,
               renames=False)
REPAIR = Tool("pdftool:repair", tr("Repair"), tr("Rebuild a damaged PDF"), repair, password=True, renames=False)
PAGE_SIZE = Tool("pdftool:pagesize", tr("Page size"), tr("Make every page the same size"), page_size,
                 password=True, renames=False)
TRIM = Tool("pdftool:trim", tr("Trim edges"), tr("Cut away the empty edges"), trim, password=True, renames=False)
