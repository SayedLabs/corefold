"""Space's fast look: an NTFS drive's own table of every file and folder (the Master File Table) read straight
from the disk, the way WizTree does, instead of asking Windows about every folder one by one. Reading the disk
itself needs an administrator, so it's done by the administrator copy of Rakiza Core (Windows asks), which writes
what it read to a file (write_listing); the window's copy reads that file back and builds the same map as an
ordinary look (build). No Qt here.

Only what the map needs is read: each record's name, the folder it's in, its size, its attributes and when it was
last changed. Nothing is ever written to the disk."""
from __future__ import annotations

import heapq
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
        data = bytearray()
        while len(data) < end - start:
            got = self.f.read(end - start - len(data))
            if not got:
                break
            data += got
        return bytes(data[offset - start:offset - start + size])

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


def read_table(path: str, cancel=None, progress=None) -> dict:
    """Every file and folder of the volume: {record: [parent, is folder, size, attributes, last change, name]}."""
    vol = Volume(path)
    try:
        table = Table(vol)
        items: dict[int, list] = {}
        extra_sizes: dict[int, int] = {}
        extra_names: dict[int, list] = {}
        for number, rec in table:
            if cancel is not None and cancel.is_set():
                break
            parsed = parse_record(rec, table.sector)
            if parsed is None:
                continue
            flags, base, names, size, attrs, changed, _ = parsed
            if base and base != number:  # (an extension record: what it holds belongs to its base record)
                if size is not None:
                    extra_sizes[base] = size
                if names:
                    extra_names.setdefault(base, []).extend(names)
                continue
            items[number] = [names, bool(flags & DIRECTORY), size, attrs, changed]
            if progress is not None and number % 100000 == 0:
                progress(number, table.records)
        out = {}
        for number, (names, is_dir, size, attrs, changed) in items.items():
            names = names or extra_names.get(number, [])
            if size is None:
                size = extra_sizes.get(number, 0)
            # the long name (Win32 or both); a DOS 8.3 name only when there's nothing else; a second hard link
            # in another folder isn't counted twice
            pick = next((n for n in names if n[1] != 2), names[0] if names else None)
            if pick is None:
                continue
            out[number] = [pick[0], is_dir, 0 if is_dir else size, attrs, changed, pick[2]]
        return out
    finally:
        vol.close()


# ----------------------------------------------------------------------------- the listing file

def write_listing(items: dict, out) -> None:
    """What read_table found, in a small binary file (the window's copy reads it back with read_listing). out is a
    path, or a file already open for writing."""
    if not isinstance(out, (str, os.PathLike)):
        _write_listing(items, out)
        return
    with open(out, "wb") as f:
        _write_listing(items, f)


def _write_listing(items: dict, f) -> None:
    f.write(LISTING_MAGIC + struct.pack("<Q", len(items)))
    pack = struct.Struct("<IIQIdH").pack
    for number, (parent, is_dir, size, attrs, changed, name) in items.items():
        raw = name.encode("utf-16-le")[:65534]
        f.write(pack(number, parent, size, (attrs & 0x7FFFFFFF) | (0x80000000 if is_dir else 0), changed, len(raw)))
        f.write(raw)


def read_listing(path: Path) -> dict:
    data = path.read_bytes()
    if data[:8] != LISTING_MAGIC:
        raise ValueError("not a listing")
    count = struct.unpack_from("<Q", data, 8)[0]
    if count > MAX_RECORDS:
        raise ValueError("too many records")
    head = struct.Struct("<IIQIdH")
    at, out = 16, {}
    for _ in range(count):
        number, parent, size, flags, changed, ln = head.unpack_from(data, at)
        at += head.size
        name = data[at:at + ln].decode("utf-16-le", "replace")
        at += ln
        out[number] = [parent, bool(flags & 0x80000000), size, flags & 0x7FFFFFFF, changed, name]
    return out


# ----------------------------------------------------------------------------- the map

def build(root: Path, items: dict, started: float | None = None):
    """The same map an ordinary look makes (space.Scan), from what read_table found on the drive at root."""
    from . import space
    started = time.monotonic() if started is None else started
    top = space.Folder(str(root))
    result = space.Scan(top)
    hidden = space._OFFLINE | space._RECALL_ACCESS | space._RECALL_OPEN
    folders: dict[int, space.Folder] = {ROOT_RECORD: top}
    children: dict[int, list[int]] = {}
    for number, item in items.items():
        if number < FIRST_USER_RECORD and number != ROOT_RECORD:
            continue
        if number != ROOT_RECORD:
            children.setdefault(item[0], []).append(number)
    # folders that lead elsewhere (junctions, links) are left out, as in an ordinary look
    reparse = 0x400
    order = [top]
    stack = [ROOT_RECORD]
    files_of: dict[int, list] = {}
    while stack:
        number = stack.pop()
        folder = folders[number]
        own = []
        for child in children.get(number, ()):
            if child < FIRST_USER_RECORD:
                continue
            parent, is_dir, size, attrs, changed, name = items[child]
            if is_dir:
                if attrs & reparse:
                    continue
                if number == ROOT_RECORD and name.lower() in ("$extend", "system volume information"):
                    pass  # (kept: an ordinary look shows them too, as folders it can't look into)
                sub = space.Folder(name, folder)
                folder.children.append(sub)
                folders[child] = sub
                order.append(sub)
                stack.append(child)
            else:
                own.append((size, name, attrs, changed))
        files_of[number] = own
    biggest: list = []
    kinds = dict.fromkeys(space.KINDS, 0)
    by_kind: dict[str, list] = {k: [] for k in space.KINDS}
    types: dict[str, list] = {}
    old: list = []
    old_before = time.time() - space.OLD_AFTER_DAYS * 86400
    for number, own in files_of.items():
        folder = folders[number]
        path = None
        kept = []
        for size, name, attrs, changed in own:
            if attrs & hidden:
                result.cloud += 1
                continue
            if path is None:
                path = str(folder.path)
            full = os.path.join(path, name)
            folder.files += 1
            folder.size += size
            result.files += 1
            kind = space.kind_of(name)
            kinds[kind] += size
            if 0 < changed < old_before and size >= 1 << 20:
                item = (size, full, changed)
                if len(old) < space.TOP_OLD:
                    heapq.heappush(old, item)
                elif size > old[0][0]:
                    heapq.heapreplace(old, item)
            of_kind = by_kind[kind]
            if len(of_kind) < space.TOP_KIND:
                heapq.heappush(of_kind, (size, full))
            elif size > of_kind[0][0]:
                heapq.heapreplace(of_kind, (size, full))
            ext = space.type_of(name)
            of_type = types.get(ext)
            if of_type is None:
                of_type = types[ext] = [0, 0, []]
            of_type[0] += size
            of_type[1] += 1
            if len(of_type[2]) < space.TOP_TYPE:
                heapq.heappush(of_type[2], (size, full))
            elif size > of_type[2][0][0]:
                heapq.heapreplace(of_type[2], (size, full))
            kept.append((size, name, changed))
            if len(biggest) < space.TOP_OVERALL:
                heapq.heappush(biggest, (size, full))
            elif size > biggest[0][0]:
                heapq.heapreplace(biggest, (size, full))
        folder.set_files(kept)
    for f in reversed(order):
        f.children.sort(key=lambda c: c.size, reverse=True)
        p = f.parent
        if p is not None:
            p.size += f.size
            p.files += f.files
            p.folders += f.folders + 1
            if f.newest > p.newest:
                p.newest = f.newest
    result.folders = top.folders
    result.biggest = sorted(biggest, reverse=True)
    result.kinds = kinds
    result.biggest_kind = {k: sorted(v, reverse=True) for k, v in by_kind.items()}
    result.old = sorted(old, reverse=True)
    kept_types = sorted(types.items(), key=lambda kv: kv[1][0], reverse=True)[:space.KEEP_TYPES]
    result.types = {ext: (v[0], v[1]) for ext, v in kept_types}
    result.biggest_type = {ext: sorted(v[2], reverse=True) for ext, v in kept_types}
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
