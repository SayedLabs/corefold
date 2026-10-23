"""Reading email files: .eml (the standard format) and .msg (Outlook's).

Everything here is plain Python with no outside libraries: a small reader for Outlook's container
format (Microsoft's "Compound File", [MS-CFB]), its compressed RTF ([MS-OXRTFCP]) and the HTML kept
inside that RTF ([MS-OXRTFEX]). Emails come from strangers, so every size and every link between
parts is checked, and nothing in an email can make this code loop forever or read outside the file.
"""
from __future__ import annotations

import datetime as dt
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE = 300 << 20       # emails bigger than this aren't opened
MAX_PART = 200 << 20       # no single part is read past this


class MailError(Exception):
    pass


@dataclass
class Attachment:
    name: str
    data: bytes = b""
    mime: str = ""
    content_id: str = ""        # for pictures shown inside the email (cid:…)
    inline: bool = False
    message: "Message | None" = None  # an email attached to the email (Outlook)

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class Message:
    subject: str = ""
    sender: str = ""
    to: str = ""
    cc: str = ""
    date: dt.datetime | None = None
    html: str = ""
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    headers: str = ""

    def visible_attachments(self) -> list[Attachment]:
        """The attachments people expect to see (not the pictures that are part of the email's design)."""
        used = set(re.findall(r"cid:([^\"'\s>)]+)", self.html, re.I))
        return [a for a in self.attachments if not (a.content_id and a.content_id in used)]


def read(path: Path) -> Message:
    size = path.stat().st_size
    if size > MAX_FILE:
        raise MailError("this email file is too big to open")
    data = path.read_bytes()
    try:
        if data[:8] == CFB_MAGIC:
            return read_msg(data)
        return read_eml(data)
    except MailError:
        raise
    except (struct.error, IndexError, ValueError, UnicodeError, OverflowError, MemoryError, RecursionError) as e:
        raise MailError("this email file is damaged") from e


# ----------------------------------------------------------------------------- .eml

def read_eml(data: bytes) -> Message:
    import email
    from email import policy
    from email.utils import parsedate_to_datetime
    msg = email.message_from_bytes(data, policy=policy.default)
    m = Message(subject=_header(msg, "subject"), sender=_header(msg, "from"), to=_header(msg, "to"),
                cc=_header(msg, "cc"))
    try:
        if msg["date"]:
            m.date = parsedate_to_datetime(str(msg["date"]))
    except (TypeError, ValueError, IndexError):
        m.date = None
    m.headers = "".join(f"{k}: {v}\n" for k, v in msg.items())[:20000]
    body_html = msg.get_body(preferencelist=("html",))
    body_text = msg.get_body(preferencelist=("plain",))
    if body_html is not None:
        m.html = _part_text(body_html)
    if body_text is not None:
        m.text = _part_text(body_text)
    for part in msg.iter_attachments():
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            payload = b""
        if part.get_content_type() == "message/rfc822" and not payload:
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            try:
                payload = inner.as_bytes() if inner is not None else b""
            except Exception:
                payload = b""
        name = part.get_filename() or ""
        cid = (part.get("content-id") or "").strip().strip("<>")
        if not name:
            ext = {"message/rfc822": ".eml"}.get(part.get_content_type(), "")
            name = (cid.split("@")[0] if cid else "attachment") + ext
        m.attachments.append(Attachment(_clean_name(name), payload[:MAX_PART], part.get_content_type(), cid,
                                        part.get_content_disposition() == "inline"))
    # pictures that are part of the email's design (inside its HTML part, found by Content-ID)
    have = {a.content_id for a in m.attachments if a.content_id}
    for part in msg.walk():
        cid = (part.get("content-id") or "").strip().strip("<>")
        if not cid or cid in have or part.is_multipart() or part.get_content_maintype() == "text":
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            continue
        have.add(cid)
        m.attachments.append(Attachment(_clean_name(part.get_filename() or cid.split("@")[0]), payload[:MAX_PART],
                                        part.get_content_type(), cid, True))
    return m


def _header(msg, name: str) -> str:
    try:
        value = msg[name]
        return str(value) if value is not None else ""
    except Exception:
        return ""


def _part_text(part) -> str:
    try:
        return part.get_content()
    except Exception:
        raw = part.get_payload(decode=True) or b""
        return raw.decode(part.get_content_charset() or "utf-8", "replace")


def _clean_name(name: str) -> str:
    """A safe file name: no folders, no characters Windows refuses, never empty."""
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name.replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")
    return name[:180] or "attachment"


# ----------------------------------------------------------------------------- Compound File (.msg)

CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ENDOFCHAIN, FREESECT, NOSTREAM = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFF


@dataclass
class _Entry:
    name: str
    kind: int        # 1 storage, 2 stream, 5 root
    left: int
    right: int
    child: int
    start: int
    size: int


class CompoundFile:
    """Reads the streams of a Microsoft Compound File (the container .msg files use)."""

    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != CFB_MAGIC:
            raise MailError("not an Outlook email file")
        self.data = data
        (sector_shift, mini_shift) = struct.unpack_from("<HH", data, 0x1E)
        if sector_shift not in (9, 12) or mini_shift != 6:
            raise MailError("this Outlook file is damaged")
        self.ss = 1 << sector_shift
        self.mss = 1 << mini_shift
        (n_fat, first_dir) = struct.unpack_from("<II", data, 0x2C)
        (self.cutoff, first_minifat, n_minifat, first_difat, n_difat) = struct.unpack_from("<IIIII", data, 0x38)
        self.n_sectors = max(0, (len(data) - self.ss) // self.ss)
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sector, seen = first_difat, set()
        while sector not in (ENDOFCHAIN, FREESECT) and len(seen) < n_difat + 1 and sector < self.n_sectors:
            if sector in seen:
                break
            seen.add(sector)
            block = self._sector(sector)
            values = struct.unpack(f"<{self.ss // 4}I", block)
            difat.extend(values[:-1])
            sector = values[-1]
        fat_sectors = [s for s in difat if s not in (FREESECT, ENDOFCHAIN)][:n_fat]
        fat: list[int] = []
        for s in fat_sectors:
            if s >= self.n_sectors:
                raise MailError("this Outlook file is damaged")
            fat.extend(struct.unpack(f"<{self.ss // 4}I", self._sector(s)))
        self.fat = fat
        dir_bytes = self._chain(first_dir, self.fat, self._sector)
        self.entries: list[_Entry] = []
        for i in range(len(dir_bytes) // 128):
            raw = dir_bytes[i * 128:(i + 1) * 128]
            name_len = struct.unpack_from("<H", raw, 0x40)[0]
            name = raw[:max(0, min(64, name_len) - 2)].decode("utf-16-le", "replace")
            kind = raw[0x42]
            left, right, child = struct.unpack_from("<III", raw, 0x44)
            start = struct.unpack_from("<I", raw, 0x74)[0]
            size = struct.unpack_from("<Q", raw, 0x78)[0]
            if self.ss == 512:
                size &= 0xFFFFFFFF  # version 3 files only use the low half
            self.entries.append(_Entry(name, kind, left, right, child, start, size))
        if not self.entries or self.entries[0].kind != 5:
            raise MailError("this Outlook file is damaged")
        root = self.entries[0]
        self.ministream = self._chain(root.start, self.fat, self._sector, root.size) if root.size else b""
        self.minifat: list[int] = []
        if n_minifat and first_minifat not in (ENDOFCHAIN, FREESECT):
            raw = self._chain(first_minifat, self.fat, self._sector)
            self.minifat = list(struct.unpack(f"<{len(raw) // 4}I", raw[:len(raw) // 4 * 4]))

    def _sector(self, n: int) -> bytes:
        start = (n + 1) * self.ss
        return self.data[start:start + self.ss]

    def _mini(self, n: int) -> bytes:
        start = n * self.mss
        return self.ministream[start:start + self.mss]

    def _chain(self, start: int, table: list[int], read, size: int | None = None) -> bytes:
        out = bytearray()
        sector, steps = start, 0
        limit = len(table) + 1
        cap = MAX_PART if size is None else min(size, MAX_PART)
        while sector not in (ENDOFCHAIN, FREESECT) and sector < len(table):
            steps += 1
            if steps > limit:  # a loop in a damaged file
                break
            out += read(sector)
            if len(out) >= cap:
                break
            sector = table[sector]
        return bytes(out[:size]) if size is not None else bytes(out)

    def children(self, index: int) -> dict[str, int]:
        """The streams and storages directly inside storage number index: name -> entry number."""
        found: dict[str, int] = {}
        start = self.entries[index].child if 0 <= index < len(self.entries) else NOSTREAM
        stack, seen = [start], set()
        while stack:
            i = stack.pop()
            if i == NOSTREAM or i >= len(self.entries) or i in seen:
                continue
            seen.add(i)
            e = self.entries[i]
            found[e.name] = i
            stack += [e.left, e.right]
        return found

    def read(self, index: int) -> bytes:
        e = self.entries[index]
        if e.kind != 2:
            return b""
        if e.size < self.cutoff:
            return self._chain(e.start, self.minifat, self._mini, e.size)
        return self._chain(e.start, self.fat, self._sector, e.size)


# ----------------------------------------------------------------------------- Outlook message

PT_UNICODE, PT_STRING8, PT_BINARY = 0x001F, 0x001E, 0x0102
PT_SYSTIME, PT_LONG, PT_BOOLEAN = 0x0040, 0x0003, 0x000B


class _Props:
    """The properties of one part of a .msg (the message, an attachment, a recipient)."""

    def __init__(self, cf: CompoundFile, storage: int, header: int, codepage: str = "cp1252"):
        self.cf, self.storage = cf, storage
        self.kids = cf.children(storage)
        self.codepage = codepage
        self.fixed: dict[int, tuple[int, bytes]] = {}
        index = self.kids.get("__properties_version1.0")
        if index is not None:
            raw = cf.read(index)[header:]
            for off in range(0, len(raw) - 15, 16):
                tag, _flags = struct.unpack_from("<II", raw, off)
                self.fixed[tag >> 16] = (tag & 0xFFFF, raw[off + 8:off + 16])

    def stream(self, prop: int, kind: int) -> bytes | None:
        index = self.kids.get(f"__substg1.0_{prop:04X}{kind:04X}")
        return self.cf.read(index) if index is not None else None

    def text(self, prop: int) -> str:
        raw = self.stream(prop, PT_UNICODE)
        if raw is not None:
            return raw.decode("utf-16-le", "replace").rstrip("\x00")
        raw = self.stream(prop, PT_STRING8)
        if raw is not None:
            try:
                return raw.decode(self.codepage, "replace").rstrip("\x00")
            except LookupError:
                return raw.decode("cp1252", "replace").rstrip("\x00")
        return ""

    def binary(self, prop: int) -> bytes:
        return self.stream(prop, PT_BINARY) or b""

    def number(self, prop: int) -> int | None:
        kind, value = self.fixed.get(prop, (0, b""))
        if kind in (PT_LONG, PT_BOOLEAN) and len(value) >= 4:
            return struct.unpack_from("<i", value)[0]
        return None

    def time(self, prop: int) -> dt.datetime | None:
        kind, value = self.fixed.get(prop, (0, b""))
        if kind != PT_SYSTIME or len(value) < 8:
            return None
        ticks = struct.unpack_from("<Q", value)[0]
        try:
            return (dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=ticks // 10))
        except OverflowError:
            return None


CODEPAGES = {65001: "utf-8", 1252: "cp1252", 1256: "cp1256", 1251: "cp1251", 1250: "cp1250", 1253: "cp1253",
             1254: "cp1254", 1255: "cp1255", 1257: "cp1257", 932: "cp932", 936: "gbk", 949: "cp949", 950: "cp950",
             874: "cp874", 20127: "ascii", 28591: "latin-1"}


def read_msg(data: bytes) -> Message:
    return _message(CompoundFile(data), 0, 32, depth=0)


def _message(cf: CompoundFile, storage: int, header: int, depth: int) -> Message:
    p = _Props(cf, storage, header)
    cp = p.number(0x3FFD) or p.number(0x3FDE)
    if cp:
        p.codepage = CODEPAGES.get(cp, f"cp{cp}")
    m = Message()
    m.subject = p.text(0x0037)
    name = p.text(0x0C1A) or p.text(0x0042)
    email_addr = p.text(0x5D01) or p.text(0x0C1F) or p.text(0x0065)
    if email_addr and "@" not in email_addr:  # an Exchange address, not one people can read
        email_addr = ""
    m.sender = f"{name} <{email_addr}>" if name and email_addr and email_addr not in name else (name or email_addr)
    m.to = p.text(0x0E04)
    m.cc = p.text(0x0E03)
    m.date = p.time(0x0039) or p.time(0x0E06) or p.time(0x3007)
    m.headers = p.text(0x007D)[:20000]
    if not m.date and m.headers:
        found = re.search(r"^Date:\s*(.+)$", m.headers, re.M | re.I)
        if found:
            from email.utils import parsedate_to_datetime
            try:
                m.date = parsedate_to_datetime(found.group(1).strip())
            except (TypeError, ValueError, IndexError):
                pass
    m.text = p.text(0x1000)
    html = p.binary(0x1013) or p.text(0x1013).encode("utf-8")
    if html:
        m.html = _decode_html(html, p.codepage)
    if not m.html:
        rtf = p.binary(0x1009)
        if rtf:
            try:
                plain_rtf = decompress_rtf(rtf)
                kind, body = rtf_body(plain_rtf)
                if kind == "html":
                    m.html = body
                elif not m.text:
                    m.text = body
            except MailError:
                pass
    for name_, index in sorted(p.kids.items()):
        if name_.startswith("__attach_version1.0_#") and cf.entries[index].kind == 1:
            att = _attachment(cf, index, p.codepage, depth)
            if att is not None:
                m.attachments.append(att)
    return m


def _attachment(cf: CompoundFile, storage: int, codepage: str, depth: int) -> Attachment | None:
    a = _Props(cf, storage, 8, codepage)
    if a.number(0x7FFE) == 1 and not a.binary(0x3701):  # hidden, with nothing in it
        return None
    name = a.text(0x3707) or a.text(0x3704) or a.text(0x3001) or "attachment"
    method = a.number(0x3705)
    cid = a.text(0x3712).strip().strip("<>")
    mime = a.text(0x370E)
    if method == 5:  # an email inside the email
        inner = a.kids.get("__substg1.0_3701000D")
        if inner is not None and depth < 3:
            try:
                msg = _message(cf, inner, 24, depth + 1)
            except (MailError, struct.error, IndexError):
                msg = None
            if msg is not None:
                title = msg.subject or name
                return Attachment(_clean_name(title) + ".eml", to_eml(msg), "message/rfc822", cid, False, msg)
        return None
    return Attachment(_clean_name(name), a.binary(0x3701), mime, cid, bool(cid))


def _decode_html(raw: bytes, codepage: str) -> str:
    raw = raw[:MAX_PART]
    found = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", raw[:4000], re.I)
    for enc in ([found.group(1).decode("ascii", "ignore")] if found else []) + ["utf-8", codepage, "cp1252"]:
        try:
            return raw.decode(enc).rstrip("\x00")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def to_eml(m: Message) -> bytes:
    """An email read from an Outlook file, written as a standard .eml (to save an attached email)."""
    from email.message import EmailMessage
    from email.utils import format_datetime
    e = EmailMessage()
    for key, value in (("Subject", m.subject), ("From", m.sender), ("To", m.to), ("Cc", m.cc)):
        if value:
            try:
                e[key] = value.replace("\r", " ").replace("\n", " ")
            except (ValueError, TypeError):
                pass
    if m.date:
        try:
            e["Date"] = format_datetime(m.date)
        except (ValueError, TypeError):
            pass
    e.set_content(m.text or "")
    if m.html:
        e.add_alternative(m.html, subtype="html")
    for a in m.attachments:
        maintype, _, subtype = (a.mime or "application/octet-stream").partition("/")
        try:
            e.add_attachment(a.data, maintype=maintype or "application", subtype=subtype or "octet-stream",
                             filename=a.name)
        except (ValueError, TypeError):
            pass
    return bytes(e)


# ----------------------------------------------------------------------------- compressed RTF

_RTF_PREBUF = (b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern \\fscript "
               b"\\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0\\blue0\r\n\\par "
               b"\\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx")


def decompress_rtf(data: bytes) -> bytes:
    """[MS-OXRTFCP]: the RTF body Outlook keeps compressed."""
    if len(data) < 16:
        raise MailError("damaged RTF")
    comp_size, raw_size, kind = struct.unpack_from("<III", data)
    if kind == 0x414C454D:  # "MELA": stored as is
        return data[16:16 + raw_size]
    if kind != 0x75465A4C:  # "LZFu"
        raise MailError("unknown RTF compression")
    raw_size = min(raw_size, MAX_PART)
    window = bytearray(4096)
    window[:len(_RTF_PREBUF)] = _RTF_PREBUF
    write = len(_RTF_PREBUF)
    out = bytearray()
    src, end = 16, min(len(data), comp_size + 4)
    while src < end and len(out) < raw_size:
        control = data[src]
        src += 1
        for bit in range(8):
            if src >= end or len(out) >= raw_size:
                break
            if control & (1 << bit):
                if src + 1 >= end:
                    return bytes(out)
                ref = (data[src] << 8) | data[src + 1]
                src += 2
                offset, length = ref >> 4, (ref & 0x0F) + 2
                if offset == write:
                    return bytes(out)
                for i in range(length):
                    ch = window[(offset + i) % 4096]
                    out.append(ch)
                    window[write] = ch
                    write = (write + 1) % 4096
            else:
                ch = data[src]
                src += 1
                out.append(ch)
                window[write] = ch
                write = (write + 1) % 4096
    return bytes(out)


_RTF_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}]+)", re.S)
_SKIP_DESTINATIONS = {b"fonttbl", b"colortbl", b"stylesheet", b"info", b"pict", b"object", b"themedata",
                      b"colorschememapping", b"latentstyles", b"datastore", b"listtable", b"listoverridetable",
                      b"rsidtbl", b"generator", b"xmlnstbl", b"mmathPr", b"header", b"footer", b"fldinst"}


def rtf_body(rtf: bytes) -> tuple[str, str]:
    """("html", the HTML Outlook kept inside the RTF) or ("text", the RTF's plain text)."""
    from_html = b"\\fromhtml" in rtf[:2000]
    cp = "cp1252"
    found = re.search(rb"\\ansicpg(\d+)", rtf[:2000])
    if found:
        cp = CODEPAGES.get(int(found.group(1)), f"cp{int(found.group(1))}")
    out: list[str] = []
    pending = bytearray()  # \'xx bytes waiting to be decoded together (multi-byte code pages)

    def flush():
        if pending:
            try:
                out.append(pending.decode(cp, "replace"))
            except LookupError:
                out.append(pending.decode("cp1252", "replace"))
            pending.clear()

    # each group remembers: skip it? inside \htmlrtf (RTF-only text)? how many characters follow \uN?
    stack: list[list] = []
    skip, htmlrtf, uc, ignore_next = False, False, 1, 0
    for m in _RTF_TOKEN.finditer(rtf[:MAX_PART]):
        word, num, hexbyte, sym, brace, text = m.groups()
        if brace == b"{":
            stack.append([skip, htmlrtf, uc])
            continue
        if brace == b"}":
            flush()
            if stack:
                skip, htmlrtf, uc = stack.pop()
            continue
        if ignore_next and (hexbyte or text):
            if text:
                take = min(ignore_next, len(text))
                ignore_next -= take
                text = text[take:]
                if not text:
                    continue
            else:
                ignore_next -= 1
                continue
        quiet = skip or (from_html and htmlrtf)
        if word is not None:
            if word in _SKIP_DESTINATIONS:
                skip = True
            elif word == b"htmlrtf":
                htmlrtf = num != b"0"
            elif word == b"htmltag" or word == b"mhtmltag":
                pass  # what follows is HTML (kept)
            elif word == b"uc" and num is not None:
                uc = int(num)
            elif word == b"u" and num is not None:
                if not quiet:
                    flush()
                    out.append(chr(int(num) % 0x10000))
                ignore_next = uc
            elif not quiet:
                flush()
                if word in (b"par", b"line"):
                    out.append("\n" if not from_html else "\r\n")
                elif word == b"tab":
                    out.append("\t")
            continue
        if sym is not None:
            if sym == b"*":
                if stack and not from_html:
                    skip = True
                continue
            if not quiet and sym in (b"\\", b"{", b"}"):
                flush()
                out.append(sym.decode())
            elif not quiet and sym == b"~":
                flush()
                out.append("\u00a0")
            continue
        if hexbyte is not None:
            if not quiet:
                pending.append(int(hexbyte, 16))
            continue
        if text is not None and not quiet:
            flush()
            out.append(text.replace(b"\r", b"").replace(b"\n", b"").decode(cp, "replace"))
    flush()
    body = "".join(out)
    return ("html", body) if from_html else ("text", body.strip())
