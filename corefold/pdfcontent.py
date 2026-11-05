"""Rakiza Core PDF's editor: getting a page ready before PDFium changes it, so nothing else on the page changes.

PDFium can take a page's pieces out, move them or give them another colour, but it then writes again every
piece of the page's drawing instructions (its content stream) the changed piece is in, and it can't write all
of them as they were: colour gradients and patterns are lost, text in fonts the PDF draws itself (Type 3:
charts made with matplotlib, old LaTeX files) comes out broken and breaks what follows it, and the 14 standard
fonts lose letters the PDF gave other names. So, on a page that's changed, before PDFium opens it:

- its content is cut into many small streams, one after the other (a PDF draws them in turn, so the page looks
  just the same). PDFium then writes again only the small streams a changed piece is in, and everything else
  stays exactly as it was. Settings the page keeps for later (a colour, a font) are never lost with them: they're
  set again right after each stream that PDFium might write again.
- a group of pieces (a form XObject) with a piece in it that's changed is put into the page itself, the way it
  would be drawn (its place, its edges), so that piece is one of the page's own and the rest stays as it was.

Nothing here draws anything: it only moves the page's own instructions around."""
from __future__ import annotations

import logging
import math

log = logging.getLogger("corefold")

_PATH_BUILD = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}
_PATH_PAINT = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"}
_CLIP = {b"W", b"W*"}
_PIECES = {b"Do", b"INLINE IMAGE", b"sh"}
# kept where they are, in streams PDFium never writes again (they hold the page's state: saved and restored
# settings, the place things are drawn at, marked parts, Type 3 glyph widths)
_FIXED = {b"q", b"Q", b"cm", b"BMC", b"BDC", b"EMC", b"MP", b"DP", b"BX", b"EX", b"d0", b"d1"}
_TEXT_ONLY = {b"Td", b"TD", b"Tm", b"T*", b"Tj", b"TJ", b"'", b'"'}
# settings set again after a stream PDFium may write again (setting them twice changes nothing)
_SETTINGS = {b"w", b"J", b"j", b"M", b"d", b"ri", b"i", b"gs", b"CS", b"cs", b"SC", b"SCN", b"sc", b"scn",
             b"G", b"g", b"RG", b"rg", b"K", b"k", b"Tc", b"Tw", b"Tz", b"TL", b"Tf", b"Tr", b"Ts"}
MOST_CHUNKS = 250       # a page is cut into at most about this many streams of pieces
MOST_STREAMS = 4000     # (a page that would need more is left as it is: PDFium writes it whole, as before)
_RESOURCES = ("/Font", "/XObject", "/ExtGState", "/ColorSpace", "/Pattern", "/Shading", "/Properties")


# ----------------------------------------------------------------------------- cutting a page's content

def _settings_of(ops: list) -> list:
    """The settings a run of instructions leaves behind, as instructions that set them again."""
    from pypdf.generic import FloatObject
    out = []
    for operands, op in ops:
        if op in _SETTINGS:
            out.append((operands, op))
        elif op == b'"' and len(operands) >= 3:  # (word and letter spacing, set with the text)
            out.append(([operands[0]], b"Tw"))
            out.append(([operands[1]], b"Tc"))
        elif op == b"TD" and len(operands) >= 2:  # (TD sets the line spacing too)
            try:
                out.append(([FloatObject(-float(operands[1]))], b"TL"))
            except (TypeError, ValueError):
                pass
    return out


def _segments(ops: list) -> list[tuple[str, list]]:
    """The page's instructions in runs: ("piece", …) draws pieces and nothing else, ("set", …) only changes
    settings that can be set again, ("fixed", …) is everything that must stay exactly where it is."""
    segs: list[tuple[str, list]] = []
    i, n = 0, len(ops)
    while i < n:
        operands, op = ops[i]
        if op == b"BT":
            j = i + 1
            while j < n and ops[j][1] not in (b"ET", b"BT"):
                j += 1
            if j >= n or ops[j][1] != b"ET":
                segs.append(("fixed", ops[i:j]))  # (no end to the text: left as it is)
                i = j
                continue
            block = ops[i:j + 1]
            marks = sum(1 for _, o in block if o in (b"BMC", b"BDC")) - sum(1 for _, o in block if o == b"EMC")
            clip = any(o == b"Tr" and operands_ and _number(operands_[0]) >= 4 for operands_, o in block)
            if marks or clip or any(o in (b"q", b"Q", b"cm") for _, o in block):
                segs.append(("fixed", block))  # (marked parts that run on past it, or text that clips)
            else:
                segs.append(("piece", block))
                kept = _settings_of(block)
                if kept:
                    segs.append(("set", kept))
            i = j + 1
            continue
        if op in _PATH_BUILD or op in _CLIP:
            j = i
            while j < n and (ops[j][1] in _PATH_BUILD or ops[j][1] in _CLIP):
                j += 1
            if j < n and ops[j][1] in _PATH_PAINT:
                path = ops[i:j + 1]
                clips = any(o in _CLIP for _, o in path)
                segs.append(("fixed" if clips or ops[j][1] == b"n" else "piece", path))
                i = j + 1
            else:
                segs.append(("fixed", ops[i:j]))
                i = j
            continue
        if op in _PIECES:
            segs.append(("piece", [ops[i]]))
        elif op in _SETTINGS:
            segs.append(("set", [ops[i]]))
        else:
            segs.append(("fixed", [ops[i]]))
        i += 1
    return segs


def _number(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def cut(ops: list) -> list[list] | None:
    """A page's instructions cut into the streams to write (see the top of this file), or None when it's
    better left as it is (nothing to gain, or far too many pieces)."""
    segs = _segments(ops)
    pieces = sum(1 for kind, _ in segs if kind == "piece")
    if pieces < 2:
        return None
    per = max(1, math.ceil(pieces / MOST_CHUNKS))
    streams: list[list] = []
    chunk: list = []        # pieces and settings PDFium may write again
    chunk_pieces = 0
    fixed: list = []

    def end_chunk():
        nonlocal chunk, chunk_pieces
        if chunk:
            streams.append(chunk)
            again = _settings_of(chunk)
            if again:
                streams.append(again)
        chunk, chunk_pieces = [], 0

    def end_fixed():
        nonlocal fixed
        if fixed:
            streams.append(fixed)
        fixed = []
    for kind, part in segs:
        if kind == "fixed":
            end_chunk()
            fixed += part
            continue
        end_fixed()
        if kind == "piece" and chunk_pieces >= per:
            end_chunk()
        chunk += part
        chunk_pieces += kind == "piece"
    end_chunk()
    end_fixed()
    if len(streams) > MOST_STREAMS:
        return None
    return streams


def cut_page(writer, page) -> bool:
    """Cuts one page's content into small streams (see cut). True when it was."""
    from pypdf.generic import ArrayObject, ContentStream, NameObject
    contents = page.get_contents()
    if contents is None:
        return False
    try:
        ops = ContentStream(contents, writer).operations
    except Exception:
        log.debug("a page's content couldn't be read to cut it", exc_info=True)
        return False
    streams = cut(list(ops))
    if streams is None:
        return False
    refs = ArrayObject()
    for part in streams:
        s = ContentStream(None, writer)
        s.operations = part
        refs.append(writer._add_object(s))
    page[NameObject("/Contents")] = refs
    return True


# ----------------------------------------------------------------------------- putting a group into the page

def _own_resources(writer, page):
    """The page's resources as its own (not shared with other pages, not taken from the page tree)."""
    from pypdf.generic import DictionaryObject, NameObject
    res = page.get("/Resources")
    node = page
    while res is None and node.get("/Parent") is not None:
        node = node["/Parent"].get_object()
        res = node.get("/Resources")
    mine = DictionaryObject()
    if res is not None:
        for k, v in res.get_object().items():
            mine[NameObject(k)] = v
    page[NameObject("/Resources")] = mine
    return mine


def _see_through(gs) -> bool:
    """A graphics state that makes things see-through, or blends them: a group drawn with it isn't the same
    as its pieces drawn one by one."""
    try:
        gs = gs.get_object()
        for key in ("/ca", "/CA"):
            if key in gs and float(gs[key]) < 0.999:
                return True
        if str(gs.get("/SMask", "/None")) != "/None":
            return True
        return str(gs.get("/BM", "/Normal")) not in ("/Normal", "/Compatible")
    except Exception:
        return True


def _rename(ops: list, names: dict[str, dict[str, str]]):
    """The group's resource names in its instructions, changed to the names they have in the page."""
    from pypdf.generic import NameObject

    def swap(kind: str, v):
        return NameObject(names[kind][str(v)]) if isinstance(v, NameObject) and str(v) in names.get(kind, {}) else v
    out = []
    for operands, op in ops:
        operands = list(operands) if isinstance(operands, list) else operands
        if op == b"Tf" and operands:
            operands[0] = swap("/Font", operands[0])
        elif op == b"Do" and operands:
            operands[0] = swap("/XObject", operands[0])
        elif op == b"gs" and operands:
            operands[0] = swap("/ExtGState", operands[0])
        elif op in (b"cs", b"CS") and operands:
            operands[0] = swap("/ColorSpace", operands[0])
        elif op in (b"scn", b"SCN") and operands:
            operands[-1] = swap("/Pattern", operands[-1])
        elif op == b"sh" and operands:
            operands[0] = swap("/Shading", operands[0])
        elif op in (b"BDC", b"DP") and len(operands) > 1:
            operands[1] = swap("/Properties", operands[1])
        elif op == b"INLINE IMAGE":
            settings = operands["settings"]
            for key in ("/CS", "/ColorSpace"):
                if key in settings:
                    settings[NameObject(key)] = swap("/ColorSpace", settings[key])
        out.append((operands, op))
    return out


def inline_groups(writer, page, wanted: set[int]) -> bool:
    """The page's groups (form XObjects) drawn by its own instructions, counted in the order they're drawn,
    whose numbers are in wanted: put into the page, drawn just as before. False (and nothing changed) when
    one of them can't be (see-through, shown only sometimes, or not found)."""
    from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, FloatObject, NameObject
    contents = page.get_contents()
    if contents is None:
        return False
    try:
        ops = list(ContentStream(contents, writer).operations)
    except Exception:
        log.debug("a page's content couldn't be read", exc_info=True)
        return False
    res = page.get("/Resources")
    node = page
    while res is None and node.get("/Parent") is not None:
        node = node["/Parent"].get_object()
        res = node.get("/Resources")
    res = res.get_object() if res is not None else DictionaryObject()
    xobjects = res.get("/XObject")
    xobjects = xobjects.get_object() if xobjects is not None else DictionaryObject()
    states = res.get("/ExtGState")
    states = states.get_object() if states is not None else DictionaryObject()
    found: dict[int, tuple[int, object]] = {}  # (group number: its place in ops, the group)
    count = 0
    clear = [True]
    for at, (operands, op) in enumerate(ops):
        if op == b"q":
            clear.append(clear[-1])
        elif op == b"Q" and len(clear) > 1:
            clear.pop()
        elif op == b"gs" and operands:
            gs = states.get(str(operands[0]))
            if gs is None or _see_through(gs):
                clear[-1] = False
        elif op == b"Do" and operands:
            xo = xobjects.get(str(operands[0]))
            xo = xo.get_object() if xo is not None else None
            if xo is None or xo.get("/Subtype") != "/Form":
                continue
            if count in wanted:
                if not clear[-1] or "/OC" in xo or "/Ref" in xo:
                    return False
                found[count] = (at, xo)
            count += 1
    if set(found) != set(wanted):
        return False
    mine = _own_resources(writer, page)
    made = 0
    for at, xo in sorted(found.values(), key=lambda t: -t[0]):  # (from the end: the places before stay put)
        try:
            inner = list(ContentStream(xo, writer).operations)
        except Exception:
            log.debug("a group's content couldn't be read", exc_info=True)
            return False
        names: dict[str, dict[str, str]] = {}
        own = xo.get("/Resources")
        if own is not None:
            own = own.get_object()
            for kind in _RESOURCES:
                group_names = own.get(kind)
                if group_names is None:
                    continue
                group_names = group_names.get_object()
                page_names = mine.get(kind)
                page_names = DictionaryObject(page_names.get_object()) if page_names is not None else DictionaryObject()
                for name, value in group_names.items():
                    made += 1
                    new = f"/Cf{made}{str(name)[1:]}"[:120]
                    while new in page_names:
                        made += 1
                        new = f"/Cf{made}{str(name)[1:]}"[:120]
                    page_names[NameObject(new)] = value
                    names.setdefault(kind, {})[str(name)] = new
                mine[NameObject(kind)] = page_names
        put: list = [([], b"q")]
        matrix = xo.get("/Matrix")
        if matrix is not None and [float(v) for v in matrix] != [1, 0, 0, 1, 0, 0]:
            put.append(([FloatObject(float(v)) for v in matrix], b"cm"))
        box = xo.get("/BBox")
        if box is not None and len(box) == 4:
            l, b, r, t = (float(v) for v in box)
            put.append(([FloatObject(min(l, r)), FloatObject(min(b, t)), FloatObject(abs(r - l)),
                         FloatObject(abs(t - b))], b"re"))
            put.append(([], b"W"))
            put.append(([], b"n"))
        put += _rename(inner, names)
        put.append(([], b"Q"))
        ops[at:at + 1] = put
    s = ContentStream(None, writer)
    s.operations = ops
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(s)])
    return True
