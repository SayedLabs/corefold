"""Space's fast look: an NTFS drive's own table of every file and folder (the Master File Table) read straight
from the disk, the way WizTree does, instead of asking Windows about every folder one by one. Reading the disk
itself needs an administrator, so it's done by the administrator copy of Corefold (Windows asks), which writes
what it read to a file (write_listing); the window's copy reads that file back and builds the same map as an
ordinary look (build). No Qt here.

Only what the map needs is read: each record's name, the folder it's in, its size, its attributes and when it was
last changed. Nothing is ever written to the disk."""
from __future__ import annotations

import contextlib
import gc
import logging
import os
import struct
import time
from pathlib import Path

log = logging.getLogger("corefold")

ROOT_RECORD = 5
FIRST_USER_RECORD = 16          # records 0-15 are NTFS's own ($MFT, $LogFile, …): never shown
CHUNK = 4 << 20                 # bytes of the table read at a time
DIRECTORY = 0x02                # record flag: it's a folder
IN_USE = 0x01
_EPOCH = 116444736000000000     # 1601 → 1970, in 100 ns
LISTING_MAGIC = b"CFMFT1\0\0"
MAX_RECORDS = 50_000_000        # (a listing file saying more than this is not believed)


class NotNtfs(Exception):
    pass


# ----------------------------------------------------------------------------- reading the disk

class Volume:
    """A drive opened for reading as one big file (\\\\.\\C:), or (for the tests) an NTFS image file. Reads are
    aligned to the disk's sectors, as Windows wants for a whole drive."""

    def __init__(self, path: str):
        self.f = open(path, "rb", buffering=0)
        self.sector = 512

    def read(self, offset: int, size: int) -> bytes:
        start = offset - offset % self.sector
        end = offset + size
        end += (-end) % self.sector
        self.f.seek(start)
        data = bytearray(end - start)  # (read straight into it: a piece of the table is 4 MB, read hundreds of times)
        view = memoryview(data)
        got = 0
        while got < len(data):
            n = self.f.readinto(view[got:])
            if not n:
                break
            got += n
        view.release()
        if offset == start and got == size == len(data):
            return bytes(data) if size <= 4096 else data
        return bytes(data[offset - start:min(got, offset - start + size)])

    def close(self):
        self.f.close()


def _runs(data: bytes, at: int) -> list[tuple[int, int]]:
    """A non-resident attribute's data runs: (first cluster or -1 for a hole, clusters)."""
    out, lcn = [], 0
    while at < len(data):
        head = data[at]
        if head == 0:
            break
        nlen, olen = head & 0x0F, head >> 4
        at += 1
        length = int.from_bytes(data[at:at + nlen], "little")
        at += nlen
        if olen:
            lcn += int.from_bytes(data[at:at + olen], "little", signed=True)
            out.append((lcn, length))
        else:
            out.append((-1, length))  # (sparse)
        at += olen
    return out


def _fixup(rec: bytearray, sector: int) -> bool:
    """Puts back the last two bytes of every sector of a record (NTFS keeps them aside to notice torn writes)."""
    usa, count = struct.unpack_from("<HH", rec, 4)
    if usa + 2 * count > len(rec) or count < 2:
        return False
    check = rec[usa:usa + 2]
    for i in range(1, count):
        end = i * sector - 2
        if end + 2 > len(rec):
            break
        if rec[end:end + 2] != check:
            return False
        rec[end:end + 2] = rec[usa + 2 * i:usa + 2 * i + 2]
    return True


def _filetime(v: int) -> float:
    return (v - _EPOCH) / 1e7 if v > _EPOCH else 0.0


def parse_record(rec: bytearray, sector: int):
    """One record: (flags, base record, [(parent, namespace, name)], data size or None, attributes, last change,
    attribute list present) or None when it isn't a record in use."""
    if rec[:4] != b"FILE" or not _fixup(rec, sector):
        return None
    flags = struct.unpack_from("<H", rec, 0x16)[0]
    if not flags & IN_USE:
        return None
    base = struct.unpack_from("<Q", rec, 0x20)[0] & 0xFFFFFFFFFFFF
    at = struct.unpack_from("<H", rec, 0x14)[0]
    names, size, attrs, changed, has_list = [], None, 0, 0.0, False
    n = len(rec)
    while at + 16 <= n:
        kind, length = struct.unpack_from("<II", rec, at)
        if kind == 0xFFFFFFFF or length < 16 or at + length > n:
            break
        nonres = rec[at + 8]
        name_len = rec[at + 9]
        if kind == 0x10 and not nonres:  # $STANDARD_INFORMATION
            off = struct.unpack_from("<H", rec, at + 0x14)[0]
            v = at + off
            if v + 0x24 <= n:
                changed = _filetime(struct.unpack_from("<Q", rec, v + 8)[0])
                attrs = struct.unpack_from("<I", rec, v + 0x20)[0]
        elif kind == 0x20:
            has_list = True
        elif kind == 0x30 and not nonres:  # $FILE_NAME
            off = struct.unpack_from("<H", rec, at + 0x14)[0]
            v = at + off
            if v + 0x42 <= n:
                parent = struct.unpack_from("<Q", rec, v)[0] & 0xFFFFFFFFFFFF
                ln, space = rec[v + 0x40], rec[v + 0x41]
                name = bytes(rec[v + 0x42:v + 0x42 + 2 * ln]).decode("utf-16-le", "replace")
                names.append((parent, space, name))
        elif kind == 0x80 and name_len == 0:  # the unnamed $DATA: the file's own contents
            if nonres:
                if struct.unpack_from("<Q", rec, at + 0x10)[0] == 0:  # (only the first piece holds the size)
                    size = struct.unpack_from("<Q", rec, at + 0x30)[0]
            else:
                size = struct.unpack_from("<I", rec, at + 0x10)[0]
        at += length
    return flags, base, names, size, attrs, changed, has_list


class Table:
    """The Master File Table of an NTFS volume, read in big pieces."""

    def __init__(self, vol: Volume):
        self.vol = vol
        boot = vol.read(0, 512)
        if boot[3:11] != b"NTFS    ":
            raise NotNtfs()
        self.sector = struct.unpack_from("<H", boot, 0x0B)[0]
        vol.sector = self.sector
        self.cluster = self.sector * boot[0x0D]
        mft_lcn = struct.unpack_from("<Q", boot, 0x30)[0]
        per = struct.unpack_from("<b", boot, 0x40)[0]
        self.record = 1 << -per if per < 0 else per * self.cluster
        first = bytearray(vol.read(mft_lcn * self.cluster, self.record))
        if first[:4] != b"FILE" or not _fixup(first, self.sector):
            raise NotNtfs()
        self.runs = self._data_runs(first)
        self.records = sum(n for _, n in self.runs) * self.cluster // self.record

    def _data_runs(self, rec: bytearray) -> list[tuple[int, int]]:
        at = struct.unpack_from("<H", rec, 0x14)[0]
        while at + 16 <= len(rec):
            kind, length = struct.unpack_from("<II", rec, at)
            if kind == 0xFFFFFFFF or length < 16:
                break
            if kind == 0x80 and rec[at + 8] and rec[at + 9] == 0:
                return _runs(bytes(rec), at + struct.unpack_from("<H", rec, at + 0x20)[0])
            at += length
        raise NotNtfs()

    def pieces(self):
        """(first record number, bytes) for each piece of the table, in order (whole records only)."""
        number = 0
        for lcn, clusters in self.runs:
            left = clusters * self.cluster
            pos = lcn * self.cluster
            while left > 0:
                take = min(CHUNK, left)
                take -= take % self.record
                if take <= 0:
                    break
                data = self.vol.read(pos, take) if lcn >= 0 else bytes(take)
                yield number, data
                number += len(data) // self.record
                pos += take
                left -= take

    def __iter__(self):
        """(record number, its bytes) for every record of the table, in order."""
        number = 0
        for lcn, clusters in self.runs:
            left = clusters * self.cluster
            pos = lcn * self.cluster
            while left > 0:
                take = min(CHUNK, left)
                take -= take % self.record
                if take <= 0:
                    break
                data = self.vol.read(pos, take) if lcn >= 0 else bytes(take)
                for i in range(0, len(data) - self.record + 1, self.record):
                    yield number, bytearray(data[i:i + self.record])
                    number += 1
                pos += take
                left -= take


# ----------------------------------------------------------------------------- reading the table, fast
#
# A drive has millions of records: reading them one by one in Python took 16-18 s for C: on a fast PC (WizTree: 12 s).
# They're read here a whole piece of the table at a time with numpy instead: every record of a piece is checked at
# once, then their attributes are walked side by side (one step per attribute, a dozen steps for the whole piece),
# and all their names are decoded in one go. Records that hold part of another record (a big, broken-up file, or
# a file with many names) are few: those are read the old way, one by one (parse_record).

STRIDE = 512    # NTFS keeps aside the last two bytes of every 512 bytes of a record (whatever the disk's sectors)
NO_NAME = 255   # (a record's name kind before one is found; 2 is a short DOS name, used only if there's no other)
# What a file's attributes say on the disk: Windows shows some of the same bits with another meaning. 0x40000 on the
# disk is "has extended attributes" (lots of Windows' own programs), not "recall on open": taking it for a file
# that's only in the cloud left out half of C:.
DISK_OFFLINE, DISK_RECALL_ON_DATA = 0x1000, 0x400000
CLOUD_BITS = DISK_OFFLINE | DISK_RECALL_ON_DATA
REPARSE = 0x400


@contextlib.contextmanager
def _no_gc():
    """Python's collector held back meanwhile: millions of new names would set it off again and again, each time
    looking through everything made so far (that took longer than the work itself). In the app it's held back
    anyway (see app.py); in the administrator copy it isn't."""
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


def _gather(b, rows, at, size: int):
    """The little-endian number of `size` bytes (2, 4, 6 or 8) at each row's own offset (always inside the
    record: the callers check). b is the piece's records, one row each, in one block of memory."""
    import numpy as np
    width = 8 if size == 6 else size
    flat = b.reshape(-1)
    idx = (np.asarray(rows, dtype=np.int64) * b.shape[1] + at)[:, None] + np.arange(width)
    out = flat[idx].view(f"<u{width}").ravel().astype(np.uint64)
    return out & np.uint64(0xFFFFFFFFFFFF) if size == 6 else out


class Listing:
    """Every file and folder a drive's table holds (record numbers and the record each is in), side by side in
    arrays: what read_table finds, what the administrator copy writes for the window's copy, and what build reads."""

    def __init__(self, numbers, parents, is_dir, sizes, attrs, changed, names: list[str]):
        import numpy as np
        self.numbers = np.asarray(numbers, dtype=np.uint32)
        self.parents = np.asarray(parents, dtype=np.uint32)
        self.is_dir = np.asarray(is_dir, dtype=bool)
        self.sizes = np.asarray(sizes, dtype=np.int64)
        self.attrs = np.asarray(attrs, dtype=np.uint32)
        self.changed = np.asarray(changed, dtype=np.float64)
        self.names = names

    def __len__(self) -> int:
        return len(self.names)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Listing):
            return NotImplemented
        import numpy as np
        return self.names == other.names and all(
            np.array_equal(getattr(self, k), getattr(other, k))
            for k in ("numbers", "parents", "is_dir", "sizes", "attrs", "changed"))

    def as_dict(self) -> dict:
        """{record: [folder record, is a folder, size, attributes, last change, name]} (for checks)."""
        return {int(n): [int(p), bool(d), int(s), int(a), float(c), name] for n, p, d, s, a, c, name in
                zip(self.numbers, self.parents, self.is_dir, self.sizes, self.attrs, self.changed, self.names)}


def _parse_piece(data, first_number: int, record: int):
    """Every record in use of one piece of the table: a dict of arrays (number, base, flags, parent, name, size,
    attributes, last change), or None when none is in use."""
    import numpy as np
    n = len(data) // record
    if not n:
        return None
    buf = np.frombuffer(data, dtype=np.uint8, count=n * record).reshape(n, record)
    magic = np.ascontiguousarray(buf[:, :4]).view("<u4").ravel() == 0x454C4946  # "FILE"
    flags = buf[:, 0x16].astype(np.uint32) | (buf[:, 0x17].astype(np.uint32) << 8)
    idx = np.nonzero(magic & ((flags & IN_USE) > 0))[0]
    if not len(idx):
        return None
    b = buf[idx].copy()  # (only records in use are copied: 30-40% of a big table isn't)
    m = len(idx)
    rows = np.arange(m)
    good = np.ones(m, dtype=bool)
    usa = (b[:, 4].astype(np.int64) | (b[:, 5].astype(np.int64) << 8))
    count = (b[:, 6].astype(np.int64) | (b[:, 7].astype(np.int64) << 8))
    good &= (count >= 2) & (usa + 2 * count <= record)
    usa_c = np.minimum(usa, record - 2)
    for k in range(1, record // STRIDE + 1):
        end = k * STRIDE - 2
        here = good & (k < count)
        if not here.any():
            continue
        good &= ~here | ((b[:, end] == b[rows, usa_c]) & (b[:, end + 1] == b[rows, usa_c + 1]))
        here &= good
        src = np.minimum(usa_c + 2 * k, record - 2)
        b[here, end] = b[rows[here], src[here]]
        b[here, end + 1] = b[rows[here], src[here] + 1]
    keep = np.nonzero(good)[0]
    b, idx = b[keep], idx[keep]
    m = len(idx)
    if not m:
        return None
    rows = np.arange(m)
    numbers = idx.astype(np.int64) + first_number
    flags = (b[:, 0x16].astype(np.uint32) | (b[:, 0x17].astype(np.uint32) << 8))
    base = _gather(b, rows, np.full(m, 0x20), 6).astype(np.int64)
    at = (b[:, 0x14].astype(np.int64) | (b[:, 0x15].astype(np.int64) << 8))
    alive = np.ones(m, dtype=bool)
    attrs = np.zeros(m, dtype=np.uint32)
    stamp = np.zeros(m, dtype=np.uint64)
    size = np.full(m, -1, dtype=np.int64)
    parent = np.zeros(m, dtype=np.int64)
    name_len = np.zeros(m, dtype=np.int64)
    name_at = np.zeros(m, dtype=np.int64)
    name_kind = np.full(m, NO_NAME, dtype=np.int64)
    for _ in range(64):  # (a record has a handful of attributes; 64 is far more than 1 KB can hold)
        act = np.nonzero(alive & (at + 16 <= record))[0]
        if not len(act):
            break
        a = at[act]
        both = _gather(b, act, a, 8)  # (an attribute's kind, then its length)
        kind = both & np.uint64(0xFFFFFFFF)
        length = (both >> np.uint64(32)).astype(np.int64)
        stop = (kind == 0xFFFFFFFF) | (length < 16) | (a + length > record)
        alive[act[stop]] = False
        go = ~stop
        act, a, kind, length = act[go], a[go], kind[go], length[go]
        if not len(act):
            break
        nonres = b[act, a + 8]
        named = b[act, a + 9]
        sel = (kind == 0x10) & (nonres == 0)  # $STANDARD_INFORMATION
        if sel.any():
            r, aa = act[sel], a[sel]
            v = aa + _gather(b, r, aa + 0x14, 2).astype(np.int64)
            ok = v + 0x24 <= record
            r, v = r[ok], v[ok]
            stamp[r] = _gather(b, r, v + 8, 8)
            attrs[r] = _gather(b, r, v + 0x20, 4).astype(np.uint32)
        sel = (kind == 0x30) & (nonres == 0)  # $FILE_NAME
        if sel.any():
            r, aa = act[sel], a[sel]
            v = aa + _gather(b, r, aa + 0x14, 2).astype(np.int64)
            ok = v + 0x42 <= record
            r, v = r[ok], v[ok]
            ln = b[r, v + 0x40].astype(np.int64)
            sp = b[r, v + 0x41].astype(np.int64)
            ln = np.minimum(ln, (record - (v + 0x42)) // 2)
            # the long name (Win32, or Win32 and DOS at once); a DOS 8.3 name only while there's no other
            take = (name_kind[r] == NO_NAME) | ((name_kind[r] == 2) & (sp != 2))
            r, v, ln, sp = r[take], v[take], ln[take], sp[take]
            parent[r] = _gather(b, r, v, 6).astype(np.int64)
            name_len[r] = ln
            name_at[r] = v + 0x42
            name_kind[r] = sp
        sel = (kind == 0x80) & (named == 0)  # the unnamed $DATA: the file's own contents
        if sel.any():
            r, aa, nr = act[sel], a[sel], nonres[sel] != 0
            far = nr & (aa + 0x38 <= record)
            if far.any():  # (only the first piece of a file in many pieces holds its size)
                rf, af = r[far], aa[far]
                first = _gather(b, rf, af + 0x10, 8) == 0
                size[rf[first]] = _gather(b, rf[first], af[first] + 0x30, 8).astype(np.int64)
            near = ~nr & (aa + 0x14 <= record)
            if near.any():
                size[r[near]] = _gather(b, r[near], aa[near] + 0x10, 4).astype(np.int64)
        at[act] = a + length
    # the names, all decoded at once: each followed by a 0 (a name on NTFS can't hold one) to split them again
    has = np.nonzero(name_kind != NO_NAME)[0]
    names = np.full(m, "", dtype=object)
    if len(has):
        lens = 2 * name_len[has] + 2
        total = int(lens.sum())
        starts = np.repeat(np.cumsum(lens) - lens, lens)
        pos = np.arange(total, dtype=np.int64) - starts
        col = np.repeat(name_at[has], lens) + pos
        flat = b[np.repeat(has, lens), np.minimum(col, record - 1)]
        flat[pos >= np.repeat(2 * name_len[has], lens)] = 0
        decoded = flat.tobytes().decode("utf-16-le", "replace").split("\x00")
        names[has] = np.fromiter(decoded, dtype=object, count=len(has))
    extension = (base != 0) & (base != numbers)
    ext_rows = np.nonzero(extension)[0]
    extras = [(int(base[i]), bytearray(b[i].tobytes())) for i in ext_rows]
    main = ~extension
    changed = np.where(stamp > _EPOCH, (stamp.astype(np.float64) - _EPOCH) / 1e7, 0.0)
    return {"numbers": numbers[main], "dir": (flags[main] & DIRECTORY) > 0, "parent": parent[main],
            "named": name_kind[main] != NO_NAME, "size": size[main], "attrs": attrs[main], "changed": changed[main],
            "names": names[main].tolist(), "extras": extras}


def _ahead(pieces, keep: int = 4):
    """The same pieces, read from the disk by another thread while the ones before are worked out (reading the
    disk lets go of Python's lock): a few pieces ahead at most."""
    import queue
    import threading
    q: queue.Queue = queue.Queue(keep)
    done = object()
    stop = threading.Event()

    def read():
        try:
            for piece in pieces:
                if stop.is_set():
                    return
                q.put(piece)
        except BaseException as e:  # (said where the pieces are used)
            q.put(e)
            return
        q.put(done)
    worker = threading.Thread(target=read, name="mft-read", daemon=True)
    worker.start()
    try:
        while True:
            item = q.get()
            if item is done:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        while worker.is_alive():  # (let it finish its read before the volume is closed)
            try:
                q.get(timeout=0.05)
            except queue.Empty:
                pass
        worker.join()


def read_table(path: str, cancel=None, progress=None) -> Listing:
    """Every file and folder of the volume (see Listing)."""
    with _no_gc():
        return _read_table(path, cancel, progress)


def _read_table(path: str, cancel, progress) -> Listing:
    import numpy as np
    vol = Volume(path)
    try:
        table = Table(vol)
        pieces = []
        extra_sizes: dict[int, int] = {}
        extra_names: dict[int, list] = {}
        for first, data in _ahead(table.pieces()):
            if cancel is not None and cancel.is_set():
                break
            got = _parse_piece(data, first, table.record)
            if got is not None:
                pieces.append(got)
                for base, rec in got["extras"]:  # (a record holding part of another one: read the old way)
                    parsed = parse_record(rec, STRIDE)
                    if parsed is None:
                        continue
                    _flags, _base, names, size, _attrs, _changed, _ = parsed
                    if size is not None:
                        extra_sizes[base] = size
                    if names:
                        extra_names.setdefault(base, []).extend(names)
            if progress is not None:
                progress(first, table.records)
        if not pieces:
            return Listing([], [], [], [], [], [], [])
        numbers = np.concatenate([p["numbers"] for p in pieces])
        is_dir = np.concatenate([p["dir"] for p in pieces])
        parents = np.concatenate([p["parent"] for p in pieces])
        named = np.concatenate([p["named"] for p in pieces])
        sizes = np.concatenate([p["size"] for p in pieces])
        attrs = np.concatenate([p["attrs"] for p in pieces])
        changed = np.concatenate([p["changed"] for p in pieces])
        names = [n for p in pieces for n in p["names"]]
        if extra_sizes or extra_names:
            where = {int(num): i for i, num in enumerate(numbers.tolist()) if int(num) in extra_sizes
                     or int(num) in extra_names}
            for num, i in where.items():
                if sizes[i] < 0 and num in extra_sizes:
                    sizes[i] = extra_sizes[num]
                if not named[i] and extra_names.get(num):
                    found = extra_names[num]
                    pick = next((x for x in found if x[1] != 2), found[0])
                    parents[i], names[i], named[i] = pick[0], pick[2], True
        keep = np.nonzero(named)[0]
        sizes = np.where(is_dir | (sizes < 0), 0, sizes)
        return Listing(numbers[keep], parents[keep], is_dir[keep], sizes[keep], attrs[keep], changed[keep],
                       [names[i] for i in keep.tolist()])
    finally:
        vol.close()


# ----------------------------------------------------------------------------- the listing file

LISTING_MAGIC2 = b"CFMFT2\0\0"


def write_listing(items: Listing, out) -> None:
    """What read_table found, in one binary file (the window's copy reads it back with read_listing): the arrays as
    they are, then every name, each followed by a 0. out is a path, or a file already open for writing."""
    if not isinstance(out, (str, os.PathLike)):
        _write_listing(items, out)
        return
    with open(out, "wb") as f:
        _write_listing(items, f)


def _write_listing(items: Listing, f) -> None:
    import numpy as np
    f.write(LISTING_MAGIC2 + struct.pack("<Q", len(items)))
    f.write(items.numbers.astype("<u4").tobytes())
    f.write(items.parents.astype("<u4").tobytes())
    f.write(((items.attrs & 0x7FFFFFFF) | np.where(items.is_dir, 0x80000000, 0).astype(np.uint32)).astype("<u4")
            .tobytes())
    f.write(items.sizes.astype("<i8").tobytes())
    f.write(items.changed.astype("<f8").tobytes())
    f.write("".join(n.replace("\x00", "") + "\x00" for n in items.names).encode("utf-16-le", "surrogatepass"))


def read_listing(path: Path) -> Listing:
    with _no_gc():
        return _read_listing(path)


def _read_listing(path: Path) -> Listing:
    import numpy as np
    data = path.read_bytes()
    if data[:8] != LISTING_MAGIC2:
        raise ValueError("not a listing")
    count = struct.unpack_from("<Q", data, 8)[0]
    if count > MAX_RECORDS or 16 + count * 28 > len(data):
        raise ValueError("too many records")
    at = 16

    def take(dtype, width):
        nonlocal at
        arr = np.frombuffer(data, dtype=dtype, count=count, offset=at)
        at += count * width
        return arr
    numbers, parents, flags = take("<u4", 4), take("<u4", 4), take("<u4", 4)
    sizes, changed = take("<i8", 8), take("<f8", 8)
    names = data[at:].decode("utf-16-le", "replace").split("\x00")
    if len(names) != count + 1:
        raise ValueError("names don't match")
    return Listing(numbers, parents, (flags & 0x80000000) > 0, sizes, flags & 0x7FFFFFFF, changed, names[:count])


# ----------------------------------------------------------------------------- the map

def build(root: Path, items: Listing, started: float | None = None):
    """The same map an ordinary look makes (space.Scan), from what read_table found on the drive at root. Worked out
    with numpy for every file at once; only the folders (and the few files the lists name) are Python objects."""
    with _no_gc():
        return _build(root, items, started)


def _build(root: Path, items: Listing, started: float | None):
    import numpy as np
    from . import space
    if isinstance(items, dict):  # (a listing as a dict, from older checks)
        rows = list(items.items())
        items = Listing([k for k, _ in rows], [v[0] for _, v in rows], [v[1] for _, v in rows],
                        [v[2] for _, v in rows], [v[3] for _, v in rows], [v[4] for _, v in rows],
                        [v[5] for _, v in rows])
    started = time.monotonic() if started is None else started
    top = space.Folder(str(root))
    result = space.Scan(top)
    count = len(items)
    if not count:
        result.seconds = time.monotonic() - started
        return result
    num = items.numbers.astype(np.int64)
    par = items.parents.astype(np.int64)
    usable = (num >= FIRST_USER_RECORD) & (num != ROOT_RECORD)
    # the folders: from the root down, one level at a time (folders that lead elsewhere, junctions and links, are
    # left out, as in an ordinary look, and so is everything inside them; so is a folder whose own folder is gone)
    names = items.names
    dirs = np.nonzero(usable & items.is_dir & ((items.attrs & REPARSE) == 0))[0]
    index_of = np.full(int(max(num.max(), par.max())) + 1, -1, dtype=np.int64)  # record → its folder's index
    index_of[ROOT_RECORD] = 0
    found = [np.zeros(0, dtype=np.int64)]   # (the rows of each level's folders: the root's level is empty)
    up = [np.zeros(1, dtype=np.int64)]      # (the index of each one's folder)
    count_dirs = 1
    left = dirs
    while len(left):
        p_index = index_of[par[left]]
        hit = p_index >= 0
        if not hit.any():
            break
        rows = left[hit]
        index_of[num[rows]] = np.arange(count_dirs, count_dirs + len(rows))
        count_dirs += len(rows)
        found.append(rows)
        up.append(p_index[hit])
        left = left[~hit]
    level_rows = np.concatenate(found)
    parent_index = np.concatenate([np.full(1, -1, dtype=np.int64)] + up[1:])
    level_of = np.concatenate([np.full(len(r) if i else 1, i, dtype=np.int64) for i, r in enumerate(found)])
    # the files: each in its folder (if that folder is on the map)
    files = np.nonzero(usable & ~items.is_dir)[0]
    where = index_of[par[files]]
    files, where = files[where >= 0], where[where >= 0]
    cloud = (items.attrs[files] & CLOUD_BITS) > 0
    result.cloud = int(cloud.sum())  # (only in the cloud: they take no space on this PC)
    files, where = files[~cloud], where[~cloud]
    sizes = items.sizes[files]
    times = items.changed[files].astype(np.float32)
    own_files = np.bincount(where, minlength=count_dirs)
    own_size = np.bincount(where, weights=sizes, minlength=count_dirs)
    newest = np.zeros(count_dirs, dtype=np.float64)
    if len(where):
        np.maximum.at(newest, where, times.astype(np.float64))
    # everything inside each folder: added to the one around it, deepest level first
    total_size = own_size.astype(np.int64)
    total_files = own_files.astype(np.int64)
    total_dirs = np.zeros(count_dirs, dtype=np.int64)
    bounds = np.cumsum([1] + [len(r) for r in found[1:]])
    for lv in range(len(found) - 1, 0, -1):
        span = slice(bounds[lv - 1], bounds[lv])
        ups = parent_index[span]
        np.add.at(total_size, ups, total_size[span])
        np.add.at(total_files, ups, total_files[span])
        np.add.at(total_dirs, ups, total_dirs[span] + 1)
        np.maximum.at(newest, ups, newest[span])
    # the folders themselves: each folder's ones made biggest first, so they're in the order they're shown in
    folders: list = [None] * count_dirs
    folders[0] = top
    top.size, top.files, top.folders, top.newest = int(total_size[0]), int(total_files[0]), int(total_dirs[0]), \
        float(newest[0])
    make = np.lexsort((-total_size, level_of))[1:]
    Folder = space.Folder
    for i, row, up_i, size, n_files, n_dirs, new in zip(
            make.tolist(), level_rows[make - 1].tolist(), parent_index[make].tolist(), total_size[make].tolist(),
            total_files[make].tolist(), total_dirs[make].tolist(), newest[make].tolist()):
        up_f = folders[up_i]
        f = folders[i] = Folder(names[row], up_f)
        up_f.children.append(f)
        f.size, f.files, f.folders, f.newest = size, n_files, n_dirs, new
    # each folder's own files, and the lists (biggest, kinds, types, old)
    space.finish(result, folders, where, [names[i] for i in files.tolist()], sizes, items.changed[files],
                 own_counts=False)
    result.folders = top.folders
    result.seconds = time.monotonic() - started
    return result


def drive_path(root: Path) -> str | None:
    """\\\\.\\C: for the root of a local drive (C:\\), or None when root isn't a whole drive."""
    s = str(root)
    if len(s) in (2, 3) and s[1] == ":" and s[0].isalpha() and (len(s) == 2 or s[2] in "\\/"):
        return f"\\\\.\\{s[0].upper()}:"
    return None


def is_ntfs(root: Path) -> bool:
    if os.name != "nt" or drive_path(root) is None:
        return False
    import ctypes
    buf = ctypes.create_unicode_buffer(32)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(f"{str(root)[0]}:\\", None, 0, None, None, None, buf, 32)
    if not ok or buf.value != "NTFS":
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(f"{str(root)[0]}:\\") == 3  # (a fixed disk, not a USB stick)


def helper_scan(drive: str, out: Path) -> dict:
    """In the administrator copy: reads drive's table and writes the listing to out (a new file: never one
    that's already there, nor a link someone left in its place)."""
    letter = str(drive)[:1].upper()
    if not (len(str(drive)) == 1 and "A" <= letter <= "Z"):
        return {"error": "bad drive"}
    root = Path(f"{letter}:\\")
    if not is_ntfs(root):
        return {"error": "not NTFS"}
    started = time.monotonic()
    items = read_table(f"\\\\.\\{letter}:")
    # (made here, never a file or a link that's already there, and written through this same handle: opening it
    # again by its name would follow a link someone put in its place meanwhile)
    with open(out, "xb") as f:
        write_listing(items, f)
    return {"listing": str(out), "count": len(items), "seconds": round(time.monotonic() - started, 2)}


def fast_scan(root: Path, cancel=None):
    """The fast look at a whole NTFS drive (the administrator copy reads its table: Windows asks), or None when
    it can't be done (the person said no, or anything went wrong): then the ordinary look is used."""
    if not is_ntfs(root):
        return None
    from . import uninstall as un
    from .appspage import _app_command
    started = time.monotonic()
    try:
        answer = un.run_elevated(_app_command(), "mft-scan", {"drive": str(root)[0].upper()}, cancel)
    except Exception:
        log.info("the fast look couldn't start", exc_info=True)
        return None
    if not answer or "listing" not in answer:
        log.info("the fast look didn't work: %s", answer)
        return None
    listing = un.BACKUP_DIR.parent / "helper" / Path(str(answer["listing"])).name
    try:
        if cancel is not None and cancel.is_set():
            return None
        items = read_listing(listing)
        result = build(root, items, started)
        result.fast = True
        return result
    except Exception:
        log.info("the fast look's listing couldn't be read", exc_info=True)
        return None
    finally:
        listing.unlink(missing_ok=True)
